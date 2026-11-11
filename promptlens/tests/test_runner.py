from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from promptlens.providers import (
    GenerationResult,
    Provider,
    ProviderPermanentError,
    ProviderTimeout,
)
from promptlens.registry import PromptRegistry
from promptlens.runner import run_experiment


class ScriptedProvider(Provider):
    name = "mock"
    is_mock = True

    def __init__(self, handler) -> None:
        self.handler = handler
        self.calls = 0

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> GenerationResult:
        del model, parameters, timeout
        self.calls += 1
        return self.handler(prompt, self.calls)


class RunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = PromptRegistry(self.root / "registry")
        self.prompt = self.registry.register(
            "feedback",
            "先识别用户意图\n理由要具体简洁\n用户反馈：\n${input}",
        )

    def dataset(self, rows: list[dict[str, Any]], name: str = "data.jsonl") -> Path:
        path = self.root / name
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def config(self, dataset_path: Path, **overrides: Any) -> dict[str, Any]:
        value = {
            "name": "test-run",
            "prompt_version_id": self.prompt.version_id,
            "dataset": dataset_path.name,
            "provider": "mock",
            "model": "test-rules",
            "model_params": {"temperature": 0, "seed": 7},
            "timeout_seconds": 0.5,
            "max_retries": 0,
            "retry_backoff_seconds": 0,
        }
        value.update(overrides)
        return value

    def test_mock_run_is_fully_reproducible_with_injected_clock(self) -> None:
        path = self.dataset(
            [
                {
                    "id": "a",
                    "input": "请问订单一般多久发货？",
                    "expected": "咨询｜询问发货时间",
                    "tags": ["咨询"],
                    "pattern": "^咨询｜.+$",
                },
                {
                    "id": "b",
                    "input": "建议App增加深色模式。",
                    "expected": "建议｜增加深色模式",
                    "tags": ["建议"],
                    "pattern": "^建议｜.+$",
                },
            ]
        )
        # 配置里给绝对路径，运行清单仍应只记录相对路径，避免泄露本机目录。
        config = self.config(path, dataset=str(path))
        first = run_experiment(
            config,
            self.registry,
            base_dir=self.root,
            now=lambda: "2026-01-02T03:04:05Z",
        )
        second = run_experiment(
            config,
            self.registry,
            base_dir=self.root,
            now=lambda: "2026-01-02T03:04:05Z",
        )
        self.assertEqual(first, second)
        self.assertEqual(first["summary"]["metrics"]["exact_match"]["mean"], 1.0)
        self.assertEqual(first["summary"]["total_latency_ms"], 0.0)
        self.assertEqual(len(first["manifest"]["dataset"]["sha256"]), 64)
        self.assertEqual(first["manifest"]["dataset"]["path"], "data.jsonl")
        self.assertEqual(first["created_at"], first["manifest"]["created_at"])
        self.assertTrue(all(record["estimated_tokens"]["is_estimate"] for record in first["records"]))

    def test_timeout_retries_use_injected_exponential_sleep(self) -> None:
        path = self.dataset(
            [{"id": "a", "input": "请问订单一般多久发货？", "expected": "咨询｜询问发货时间"}]
        )

        def eventually_succeeds(prompt: str, calls: int) -> GenerationResult:
            del prompt
            if calls < 3:
                raise ProviderTimeout("模拟超时")
            return GenerationResult("咨询｜询问发货时间", latency_ms=0.0)

        provider = ScriptedProvider(eventually_succeeds)
        sleeps: list[float] = []
        result = run_experiment(
            self.config(path, max_retries=2, retry_backoff_seconds=0.01),
            self.registry,
            base_dir=self.root,
            provider=provider,
            sleep=sleeps.append,
        )
        self.assertEqual(provider.calls, 3)
        self.assertEqual(sleeps, [0.01, 0.02])
        self.assertEqual(result["records"][0]["attempts"], 3)
        self.assertIsNone(result["records"][0]["error"])
        self.assertEqual(result["summary"]["failed"], 0)

    def test_permanent_failure_is_not_retried(self) -> None:
        path = self.dataset(
            [{"id": "bad", "input": "无效请求", "expected": "x"}]
        )

        def always_permanent(prompt: str, calls: int) -> GenerationResult:
            del prompt, calls
            raise ProviderPermanentError("HTTP 400：无效参数")

        provider = ScriptedProvider(always_permanent)
        sleeps: list[float] = []
        result = run_experiment(
            self.config(path, max_retries=3),
            self.registry,
            base_dir=self.root,
            provider=provider,
            sleep=sleeps.append,
        )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(result["records"][0]["attempts"], 1)
        self.assertEqual(result["records"][0]["error"]["type"], "ProviderPermanentError")

    def test_reserved_model_params_are_rejected_before_run(self) -> None:
        path = self.dataset(
            [{"id": "a", "input": "文本", "expected": "x"}]
        )
        for parameters in ({"model": "override"}, {"messages": []}):
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(ValueError, "保留字段"):
                    run_experiment(
                        self.config(path, model_params=parameters),
                        self.registry,
                        base_dir=self.root,
                    )

    def test_exhausted_failure_is_recorded_and_does_not_stop_batch(self) -> None:
        path = self.dataset(
            [
                {"id": "timeout", "input": "请问订单一般多久发货？", "expected": "咨询｜询问发货时间"},
                {"id": "ok", "input": "建议App增加深色模式。", "expected": "建议｜增加深色模式"},
            ]
        )

        def one_always_times_out(prompt: str, calls: int) -> GenerationResult:
            del calls
            if "多久发货" in prompt:
                raise ProviderTimeout("始终超时")
            return GenerationResult("建议｜增加深色模式", latency_ms=0.0)

        result = run_experiment(
            self.config(path, max_retries=1, retry_backoff_seconds=0),
            self.registry,
            base_dir=self.root,
            provider=ScriptedProvider(one_always_times_out),
            sleep=lambda _: None,
        )
        failed, succeeded = result["records"]
        self.assertEqual(failed["error"]["type"], "ProviderTimeout")
        self.assertEqual(failed["error"]["attempts"], 2)
        self.assertIsNone(failed["output"])
        self.assertEqual(failed["metrics"]["exact_match"], 0.0)
        self.assertIsNone(succeeded["error"])
        self.assertEqual(succeeded["output"], "建议｜增加深色模式")
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["summary"]["succeeded"], 1)
        self.assertEqual(result["summary"]["metrics"]["exact_match"]["mean"], 0.5)

    def test_missing_render_variable_is_an_item_error_not_a_crash(self) -> None:
        prompt = self.registry.register("needs-extra", "$input $missing")
        path = self.dataset([{"id": "a", "input": "文本", "expected": "x"}])
        result = run_experiment(
            self.config(path, prompt_version_id=prompt.version_id),
            self.registry,
            base_dir=self.root,
        )
        self.assertEqual(result["summary"]["failed"], 1)
        self.assertEqual(result["records"][0]["attempts"], 0)
        self.assertIn("缺少模板变量：missing", result["records"][0]["error"]["message"])

    def test_optional_concurrency_preserves_input_order(self) -> None:
        path = self.dataset(
            [
                {"id": "a", "input": "请问订单一般多久发货？", "expected": "咨询｜询问发货时间"},
                {"id": "b", "input": "建议App增加深色模式。", "expected": "建议｜增加深色模式"},
            ]
        )
        result = run_experiment(
            self.config(path, concurrency=2),
            self.registry,
            base_dir=self.root,
        )
        self.assertEqual([record["id"] for record in result["records"]], ["a", "b"])
        self.assertEqual(result["summary"]["metrics"]["exact_match"]["mean"], 1.0)


if __name__ == "__main__":
    unittest.main()
