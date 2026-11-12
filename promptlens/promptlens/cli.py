"""PromptLens 命令行：register、run、compare、demo。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from .registry import PromptRegistry
from .report import write_html_report
from .runner import compare_runs, run_experiment, save_json


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _load_config(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("运行配置必须是 JSON 对象")
    return raw


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path


def command_register(args: argparse.Namespace) -> int:
    registry = PromptRegistry(args.registry)
    version = registry.register(
        name=args.name,
        template=Path(args.prompt_file).read_text(encoding="utf-8"),
        description=args.description,
    )
    print(json.dumps(version.to_dict(), ensure_ascii=False, indent=2))
    return 0


def command_run(args: argparse.Namespace) -> int:
    config_path = Path(args.config).resolve()
    config = _load_config(config_path)
    registry = PromptRegistry(args.registry)
    result = run_experiment(config, registry, base_dir=config_path.parent)
    output = (
        Path(args.output).resolve()
        if args.output
        else _resolve(config_path.parent, config.get("output", "run-result.json"))
    )
    save_json(result, output)
    report_value = args.report or config.get("report")
    report_path = None
    if report_value:
        report_path = Path(report_value).resolve() if args.report else _resolve(config_path.parent, report_value)
        write_html_report(result, report_path)
    response = {
        "run_id": result["run_id"],
        "prompt_version_id": result["manifest"]["prompt"]["version_id"],
        "result": str(output),
        "report": str(report_path) if report_path else None,
        "metrics": {name: info["mean"] for name, info in result["summary"]["metrics"].items()},
        "estimated_total_cost": result["summary"]["estimated_total_cost"],
        "cost_currency": result["summary"]["cost_currency"],
    }
    print(json.dumps(response, ensure_ascii=False, indent=2))
    return 0


def command_compare(args: argparse.Namespace) -> int:
    comparison = compare_runs(args.baseline, args.candidate)
    output = Path(args.output).resolve()
    save_json(comparison, output)
    report = Path(args.report).resolve() if args.report else output.with_suffix(".html")
    write_html_report(comparison, report)
    print(
        json.dumps(
            {
                "comparison_id": comparison["comparison_id"],
                "comparison": str(output),
                "report": str(report),
                "delta": comparison["summary"]["delta"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def run_demo(output_dir: str | Path | None = None) -> dict[str, Any]:
    """从随附提示词和 JSONL 数据完整生成两次运行及对比报告。"""
    root = _project_root()
    examples = root / "examples"
    destination = Path(output_dir) if output_dir is not None else root / "artifacts"
    destination.mkdir(parents=True, exist_ok=True)
    registry = PromptRegistry(destination / "registry")
    results = []
    for config_name, output_name in (("run_v1.json", "baseline.json"), ("run_v2.json", "candidate.json")):
        config_path = examples / config_name
        config = _load_config(config_path)
        result = run_experiment(config, registry, base_dir=examples)
        save_json(result, destination / output_name)
        results.append(result)
    comparison = compare_runs(results[0], results[1])
    save_json(comparison, destination / "comparison.json")
    write_html_report(comparison, destination / "report.html")
    return {
        "output_dir": str(destination.resolve()),
        "report": str((destination / "report.html").resolve()),
        "comparison": str((destination / "comparison.json").resolve()),
        "baseline_metrics": {
            name: info["mean"] for name, info in results[0]["summary"]["metrics"].items()
        },
        "candidate_metrics": {
            name: info["mean"] for name, info in results[1]["summary"]["metrics"].items()
        },
        "delta": comparison["summary"]["delta"],
        "provider": "mock",
        "notice": "离线规则仅验证评测流水线；指标差异不构成真实模型或提示词质量提升证据。",
    }


def command_demo(args: argparse.Namespace) -> int:
    summary = run_demo(args.output_dir)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="promptlens",
        description="提示词版本管理、可复现 JSONL 实验与离线 HTML 对比报告",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    register = subparsers.add_parser("register", help="注册不可变提示词版本")
    register.add_argument("--registry", default=".promptlens/registry", help="版本注册表目录")
    register.add_argument("--name", required=True, help="提示词名称")
    register.add_argument("--prompt-file", required=True, help="UTF-8 模板文件")
    register.add_argument("--description", default="", help="版本描述（参与内容哈希）")
    register.set_defaults(func=command_register)

    run = subparsers.add_parser("run", help="按 JSON 配置运行实验")
    run.add_argument("--config", required=True, help="运行配置 JSON")
    run.add_argument("--registry", default=".promptlens/registry", help="版本注册表目录")
    run.add_argument("--output", help="覆盖结果 JSON 输出路径")
    run.add_argument("--report", help="同时生成单实验 HTML 报告")
    run.set_defaults(func=command_run)

    compare = subparsers.add_parser("compare", help="比较两个同数据集实验")
    compare.add_argument("baseline", help="基线运行结果 JSON")
    compare.add_argument("candidate", help="候选运行结果 JSON")
    compare.add_argument("--output", default="comparison.json", help="比较结果 JSON")
    compare.add_argument("--report", help="HTML 路径，默认与比较 JSON 同名")
    compare.set_defaults(func=command_compare)

    demo = subparsers.add_parser("demo", help="离线运行两个示例版本并生成对比报告")
    demo.add_argument("--output-dir", help="产物目录，默认项目 artifacts/")
    demo.set_defaults(func=command_demo)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
