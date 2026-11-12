"""可复现批量运行、失败隔离、结果持久化与实验比较。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .metrics import evaluate_item, summarize_by_tag, summarize_metrics
from .models import DatasetItem, PromptVersion, file_sha256, load_dataset
from .providers import (
    GenerationResult,
    MockProvider,
    OpenAICompatibleProvider,
    Provider,
    ProviderError,
    ProviderPermanentError,
)
from .registry import PromptRegistry

METRIC_NAMES = ("exact_match", "contains", "regex")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def estimate_tokens(text: str) -> int:
    """仅为估算：ceil(Unicode 字符数 / 4)，不声称是 tokenizer 计数。"""
    return (len(text) + 3) // 4 if text else 0


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def save_json(data: Mapping[str, Any], path: str | Path) -> Path:
    """原子写入 UTF-8 JSON，避免中途失败留下半个结果文件。"""
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, temporary_name = tempfile.mkstemp(prefix=".promptlens-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
        os.replace(temporary_name, destination)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    return destination


def _resolve_path(base_dir: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base_dir / path


def _portable_dataset_path(path: Path, base: Path) -> str:
    """清单只记录相对路径或文件名，避免把本机绝对目录写进可分享产物。"""
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return path.name


def _nonnegative_number(config: Mapping[str, Any], key: str, default: float) -> float:
    value = config.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        raise ValueError(f"运行配置 {key} 必须是非负数")
    return float(value)


def parse_pricing(config: Mapping[str, Any]) -> dict[str, Any] | None:
    """解析可选的单价配置；未配置时返回 None。

    单价是实验身份的一部分：同一提示词与数据在不同单价下会产生不同的
    成本结果，因此不能共用 run_id。价格由调用方提供，项目不内置任何
    供应商报价。
    """
    raw = config.get("pricing")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("运行配置 pricing 必须是 JSON 对象或省略")
    input_price = raw.get("input_per_1k_tokens")
    output_price = raw.get("output_per_1k_tokens")
    for key, value in (
        ("input_per_1k_tokens", input_price),
        ("output_per_1k_tokens", output_price),
    ):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            raise ValueError(f"运行配置 pricing.{key} 必须是非负数")
    currency = raw.get("currency", "USD")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("运行配置 pricing.currency 必须是非空字符串")
    label = raw.get("label", "")
    if not isinstance(label, str):
        raise ValueError("运行配置 pricing.label 必须是字符串")
    return {
        "input_per_1k_tokens": float(input_price),
        "output_per_1k_tokens": float(output_price),
        "currency": currency.strip().upper(),
        "label": label,
        "is_estimate": True,
    }


def estimate_cost(
    prompt_tokens: int,
    completion_tokens: int,
    pricing: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """按字符启发式 token 估算成本；不是供应商账单。"""
    if pricing is None:
        return None
    input_cost = prompt_tokens / 1000 * float(pricing["input_per_1k_tokens"])
    output_cost = completion_tokens / 1000 * float(pricing["output_per_1k_tokens"])
    return {
        "input": round(input_cost, 8),
        "output": round(output_cost, 8),
        "total": round(input_cost + output_cost, 8),
        "currency": pricing["currency"],
        "is_estimate": True,
        "method": "按估算 token 数 × 配置单价计算，不代表供应商账单",
    }


def run_experiment(
    config: Mapping[str, Any],
    registry: PromptRegistry,
    *,
    base_dir: str | Path | None = None,
    provider: Provider | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], str] = utc_now,
    perf_counter: Callable[[], float] = time.perf_counter,
) -> dict[str, Any]:
    """运行实验。默认串行；并发时按数据原顺序收集结果。"""
    base = Path(base_dir) if base_dir is not None else Path.cwd()
    if "dataset" not in config:
        raise ValueError("运行配置缺少 dataset")
    dataset_path = _resolve_path(base, config["dataset"])
    items = load_dataset(dataset_path)
    dataset_digest = file_sha256(dataset_path)

    if config.get("prompt_version_id"):
        prompt = registry.get(config["prompt_version_id"])
    elif config.get("prompt_file"):
        prompt_path = _resolve_path(base, config["prompt_file"])
        prompt = registry.register(
            name=config.get("prompt_name", prompt_path.stem),
            template=prompt_path.read_text(encoding="utf-8"),
            description=config.get("prompt_description", ""),
        )
    else:
        raise ValueError("运行配置必须提供 prompt_version_id 或 prompt_file")

    provider_name = config.get("provider", "mock")
    if provider is None:
        if provider_name == "mock":
            provider = MockProvider()
        elif provider_name in ("openai", "openai_compatible"):
            provider = OpenAICompatibleProvider()
        else:
            raise ValueError(f"未知 provider：{provider_name}；可选 mock 或 openai_compatible")
    if provider_name in ("openai", "openai_compatible") and provider.is_mock:
        raise ValueError("配置请求真实 OpenAI 兼容调用，但注入的是 mock provider")
    model = config.get("model") or (
        "offline-feedback-rules-v1" if provider.is_mock else getattr(provider, "default_model", "")
    )
    if not isinstance(model, str) or not model:
        raise ValueError("必须指定非空 model（OpenAI 模式也可由 OPENAI_MODEL 提供）")
    parameters = config.get("model_params", {"temperature": 0, "max_tokens": 128, "seed": 42})
    if not isinstance(parameters, dict):
        raise ValueError("model_params 必须是 JSON 对象")
    reserved = {"model", "messages"}.intersection(parameters)
    if reserved:
        raise ValueError(
            f"model_params 不得覆盖保留字段：{', '.join(sorted(reserved))}"
        )
    try:
        _canonical(parameters)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"model_params 必须可序列化为 JSON：{exc}") from exc

    pricing = parse_pricing(config)
    timeout = _nonnegative_number(config, "timeout_seconds", 10.0)
    if timeout <= 0:
        raise ValueError("timeout_seconds 必须大于 0")
    max_retries_raw = config.get("max_retries", 2)
    if isinstance(max_retries_raw, bool) or not isinstance(max_retries_raw, int) or max_retries_raw < 0:
        raise ValueError("max_retries 必须是非负整数")
    max_retries = max_retries_raw
    backoff = _nonnegative_number(config, "retry_backoff_seconds", 0.2)
    concurrency_raw = config.get("concurrency", 1)
    if isinstance(concurrency_raw, bool) or not isinstance(concurrency_raw, int) or concurrency_raw < 1:
        raise ValueError("concurrency 必须是正整数")

    name = config.get("name", "promptlens-experiment")
    if not isinstance(name, str) or not name.strip():
        raise ValueError("实验 name 必须是非空字符串")
    identity = {
        "name": name,
        "prompt_version_id": prompt.version_id,
        "dataset_sha256": dataset_digest,
        "provider": provider.name,
        "model": model,
        "model_params": parameters,
        "pricing": pricing,
    }
    run_id = "run_" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:20]
    max_attempts = max_retries + 1

    def process(item: DatasetItem) -> dict[str, Any]:
        try:
            rendered = prompt.render(item.input)
        except Exception as exc:
            metrics = evaluate_item(None, item.expected, item.pattern)
            return {
                **item.to_dict(),
                "output": None,
                "metrics": metrics,
                "latency_ms": 0.0,
                "estimated_tokens": {
                    "prompt": 0,
                    "completion": 0,
                    "total": 0,
                    "is_estimate": True,
                    "method": "ceil(Unicode字符数/4)，仅为估算",
                },
                "cost_estimate": estimate_cost(0, 0, pricing),
                "attempts": 0,
                "error": {"type": type(exc).__name__, "message": str(exc), "attempts": 0},
            }

        prompt_tokens = estimate_tokens(rendered)
        started = perf_counter()
        output: str | None = None
        measured_latency: float | None = None
        failure: Exception | None = None
        attempts = 0
        for attempts in range(1, max_attempts + 1):
            try:
                response = provider.generate(
                    rendered,
                    model=model,
                    parameters=parameters,
                    timeout=timeout,
                )
                if not isinstance(response, GenerationResult) or not isinstance(response.text, str):
                    raise ProviderError("provider 必须返回 GenerationResult，且 text 必须是字符串")
                output = response.text
                measured_latency = response.latency_ms
                failure = None
                break
            except Exception as exc:
                failure = exc
                should_retry = (
                    isinstance(exc, ProviderError)
                    and not isinstance(exc, ProviderPermanentError)
                    and attempts < max_attempts
                )
                if should_retry:
                    delay = backoff * (2 ** (attempts - 1))
                    sleep(delay)
                    continue
                break
        elapsed_ms = round(max(0.0, (perf_counter() - started) * 1000), 3)
        if failure is None and output is not None:
            error = None
            latency_ms = round(measured_latency, 3) if measured_latency is not None else elapsed_ms
        else:
            error = {
                "type": type(failure).__name__ if failure is not None else "ProviderError",
                "message": str(failure) if failure is not None else "provider 未返回输出",
                "attempts": attempts,
            }
            output = None
            latency_ms = elapsed_ms
        completion_tokens = estimate_tokens(output or "")
        return {
            **item.to_dict(),
            "output": output,
            "metrics": evaluate_item(output, item.expected, item.pattern),
            "latency_ms": latency_ms,
            "estimated_tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
                "is_estimate": True,
                "method": "ceil(Unicode字符数/4)，仅为估算",
            },
            "cost_estimate": estimate_cost(prompt_tokens, completion_tokens, pricing),
            "attempts": attempts,
            "error": error,
        }

    if concurrency_raw == 1:
        records = [process(item) for item in items]
    else:
        with ThreadPoolExecutor(max_workers=concurrency_raw, thread_name_prefix="promptlens") as executor:
            records = list(executor.map(process, items))

    estimated_total_cost = (
        round(
            sum(
                record["cost_estimate"]["total"]
                for record in records
                if record["cost_estimate"] is not None
            ),
            8,
        )
        if pricing is not None
        else None
    )
    summary = {
        "total": len(records),
        "succeeded": sum(record["error"] is None for record in records),
        "failed": sum(record["error"] is not None for record in records),
        "total_latency_ms": round(sum(record["latency_ms"] for record in records), 3),
        "estimated_total_tokens": sum(record["estimated_tokens"]["total"] for record in records),
        "token_count_is_estimate": True,
        "metrics": summarize_metrics(records),
        "estimated_total_cost": estimated_total_cost,
        "cost_currency": pricing["currency"] if pricing is not None else None,
        "cost_is_estimate": pricing is not None,
        "pricing": pricing,
    }
    reproducibility_payload = {
        "identity": identity,
        "records": [
            {
                "id": record["id"],
                "output": record["output"],
                "metrics": record["metrics"],
                "error": record["error"],
                "estimated_tokens": record["estimated_tokens"],
                "cost_estimate": record["cost_estimate"],
            }
            for record in records
        ],
        "summary_metrics": summary["metrics"],
        "estimated_total_cost": summary["estimated_total_cost"],
    }
    reproducibility_hash = hashlib.sha256(
        _canonical(reproducibility_payload).encode("utf-8")
    ).hexdigest()
    created_at = now()
    return {
        "schema_version": 1,
        "type": "promptlens_run",
        "run_id": run_id,
        "name": name,
        "created_at": created_at,
        "reproducibility_hash": reproducibility_hash,
        "manifest": {
            "created_at": created_at,
            "prompt": prompt.to_dict(),
            "dataset": {
                "path": _portable_dataset_path(dataset_path, base),
                "sha256": dataset_digest,
                "items": len(items),
            },
            "model": {
                "provider": provider.name,
                "name": model,
                "parameters": parameters,
                "is_mock": provider.is_mock,
                "notice": "离线确定性规则模拟，未调用真实模型" if provider.is_mock else "OpenAI 兼容 API 实际调用",
            },
            "execution": {
                "timeout_seconds": timeout,
                "max_retries": max_retries,
                "retry_backoff_seconds": backoff,
                "concurrency": concurrency_raw,
                "serial_reproducible": concurrency_raw == 1 and provider.is_mock,
            },
            "cost": {
                "pricing": pricing,
                "total": summary["estimated_total_cost"],
                "currency": summary["cost_currency"],
                "is_estimate": True,
                "notice": (
                    "未配置 pricing，未计算成本"
                    if pricing is None
                    else "按字符启发式 token 估算与原配置单价计算，不代表供应商账单"
                ),
            },
        },
        "records": records,
        "summary": summary,
        "by_tag": summarize_by_tag(records),
    }


def _load_run(value: Mapping[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(value, (str, Path)):
        raw = json.loads(Path(value).read_text(encoding="utf-8"))
    else:
        raw = dict(value)
    if raw.get("type") != "promptlens_run" or not isinstance(raw.get("records"), list):
        raise ValueError("不是有效的 PromptLens 运行结果")
    if "summary" not in raw or "manifest" not in raw:
        raise ValueError("运行结果缺少 summary 或 manifest")
    return raw


def _metric_delta(baseline: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, float | None]:
    deltas: dict[str, float | None] = {}
    for metric in METRIC_NAMES:
        before = baseline["metrics"][metric]["mean"] if "metrics" in baseline else baseline[metric]["mean"]
        after = candidate["metrics"][metric]["mean"] if "metrics" in candidate else candidate[metric]["mean"]
        deltas[metric] = round(after - before, 6) if before is not None and after is not None else None
    return deltas


def compare_runs(
    baseline: Mapping[str, Any] | str | Path,
    candidate: Mapping[str, Any] | str | Path,
    *,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    """按数据 ID 比较两个锁定实验，delta = candidate - baseline。"""
    before = _load_run(baseline)
    after = _load_run(candidate)
    before_sha = before["manifest"]["dataset"]["sha256"]
    after_sha = after["manifest"]["dataset"]["sha256"]
    if before_sha != after_sha:
        raise ValueError("两个实验的数据 SHA256 不同，拒绝直接比较")
    before_records = {record["id"]: record for record in before["records"]}
    after_records = {record["id"]: record for record in after["records"]}
    if len(before_records) != len(before["records"]) or len(after_records) != len(after["records"]):
        raise ValueError("运行结果内部存在重复数据 ID")
    if before_records.keys() != after_records.keys():
        missing_after = sorted(before_records.keys() - after_records.keys())
        missing_before = sorted(after_records.keys() - before_records.keys())
        raise ValueError(f"两个实验的数据 ID 不一致：candidate 缺少 {missing_after}，baseline 缺少 {missing_before}")

    records: list[dict[str, Any]] = []
    for baseline_record in before["records"]:
        item_id = baseline_record["id"]
        candidate_record = after_records[item_id]
        slim_before = {
            key: baseline_record[key]
            for key in ("output", "metrics", "latency_ms", "estimated_tokens", "attempts", "error")
        }
        slim_after = {
            key: candidate_record[key]
            for key in ("output", "metrics", "latency_ms", "estimated_tokens", "attempts", "error")
        }
        records.append(
            {
                "id": item_id,
                "input": baseline_record["input"],
                "expected": baseline_record["expected"],
                "tags": baseline_record.get("tags", []),
                "pattern": baseline_record.get("pattern"),
                "baseline": slim_before,
                "candidate": slim_after,
                "delta": {
                    metric: (
                        round(candidate_record["metrics"][metric] - baseline_record["metrics"][metric], 6)
                        if candidate_record["metrics"][metric] is not None
                        and baseline_record["metrics"][metric] is not None
                        else None
                    )
                    for metric in METRIC_NAMES
                },
            }
        )

    tags = sorted(set(before.get("by_tag", {})) | set(after.get("by_tag", {})))
    tag_comparison = {}
    for tag in tags:
        baseline_tag = before["by_tag"].get(tag, {"count": 0, "metrics": {}})
        candidate_tag = after["by_tag"].get(tag, {"count": 0, "metrics": {}})
        tag_comparison[tag] = {
            "count": max(baseline_tag["count"], candidate_tag["count"]),
            "baseline": baseline_tag["metrics"],
            "candidate": candidate_tag["metrics"],
            "delta": _metric_delta(baseline_tag, candidate_tag),
        }
    comparison_identity = {
        "baseline": before["reproducibility_hash"],
        "candidate": after["reproducibility_hash"],
    }
    before_cost = before["summary"].get("estimated_total_cost")
    after_cost = after["summary"].get("estimated_total_cost")
    cost_comparison = {
        "baseline": before_cost,
        "candidate": after_cost,
        "delta": (
            round(after_cost - before_cost, 8)
            if before_cost is not None and after_cost is not None
            else None
        ),
        "currency": after["summary"].get("cost_currency"),
        "is_estimate": after_cost is not None,
        "basis": "按字符启发式 token 估算与各实验配置单价计算，不代表供应商账单",
    }
    return {
        "schema_version": 1,
        "type": "promptlens_comparison",
        "comparison_id": "cmp_" + hashlib.sha256(_canonical(comparison_identity).encode("utf-8")).hexdigest()[:20],
        "created_at": now(),
        "dataset_sha256": before_sha,
        "baseline": {
            "run_id": before["run_id"],
            "name": before["name"],
            "created_at": before["created_at"],
            "reproducibility_hash": before["reproducibility_hash"],
            "manifest": before["manifest"],
            "summary": before["summary"],
        },
        "candidate": {
            "run_id": after["run_id"],
            "name": after["name"],
            "created_at": after["created_at"],
            "reproducibility_hash": after["reproducibility_hash"],
            "manifest": after["manifest"],
            "summary": after["summary"],
        },
        "summary": {
            "baseline": before["summary"]["metrics"],
            "candidate": after["summary"]["metrics"],
            "delta": _metric_delta(before["summary"], after["summary"]),
        },
        "cost": cost_comparison,
        "by_tag": tag_comparison,
        "records": records,
    }
