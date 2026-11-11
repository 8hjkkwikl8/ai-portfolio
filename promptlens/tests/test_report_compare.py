from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Mapping

from promptlens.providers import GenerationResult, Provider
from promptlens.registry import PromptRegistry
from promptlens.report import write_html_report
from promptlens.runner import compare_runs, run_experiment, save_json


class StaticProvider(Provider):
    name = "mock"
    is_mock = True

    def __init__(self, dangerous_output: str) -> None:
        self.dangerous_output = dangerous_output

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> GenerationResult:
        del model, parameters, timeout
        if prompt == "one":
            return GenerationResult("OK", latency_ms=0.0)
        return GenerationResult(self.dangerous_output, latency_ms=0.0)


class ReportAndComparisonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = PromptRegistry(self.root / "registry")
        prompt = self.registry.register("test", "${input}")
        dataset = self.root / "data.jsonl"
        rows = [
            {"id": "safe", "input": "one", "expected": "OK", "tags": ["共同"]},
            {
                "id": "xss",
                "input": "<script>alert(\"x\")</script>",
                "expected": "<b>expected</b>",
                "tags": ["共同", "转义"],
            },
        ]
        dataset.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        config = {
            "name": "baseline",
            "prompt_version_id": prompt.version_id,
            "dataset": "data.jsonl",
            "provider": "mock",
            "model": "static-test",
            "max_retries": 0,
        }
        fixed_time = lambda: "2026-01-01T00:00:00Z"
        self.baseline = run_experiment(
            config,
            self.registry,
            base_dir=self.root,
            provider=StaticProvider("<img src=x onerror=alert(1)>"),
            now=fixed_time,
        )
        self.candidate = run_experiment(
            {**config, "name": "candidate"},
            self.registry,
            base_dir=self.root,
            provider=StaticProvider("<b>expected</b>"),
            now=fixed_time,
        )

    def test_comparison_delta_and_per_item_delta(self) -> None:
        comparison = compare_runs(
            self.baseline,
            self.candidate,
            now=lambda: "2026-01-01T00:00:00Z",
        )
        self.assertEqual(comparison["summary"]["baseline"]["exact_match"]["mean"], 0.5)
        self.assertEqual(comparison["summary"]["candidate"]["exact_match"]["mean"], 1.0)
        self.assertEqual(comparison["summary"]["delta"]["exact_match"], 0.5)
        self.assertEqual(comparison["records"][1]["delta"]["exact_match"], 1.0)
        self.assertIn("共同", comparison["by_tag"])
        self.assertIsNone(comparison["summary"]["delta"]["regex"])

    def test_report_escapes_input_output_and_expected(self) -> None:
        comparison = compare_runs(self.baseline, self.candidate)
        report_path = write_html_report(comparison, self.root / "report.html")
        report = report_path.read_text(encoding="utf-8")
        self.assertIn('<html lang="zh-CN">', report)
        self.assertIn("提示词实验对比报告", report)
        self.assertNotIn("<script>alert", report)
        self.assertNotIn("<img src=x", report)
        self.assertNotIn("<b>expected</b>", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", report)
        self.assertIn("&lt;b&gt;expected&lt;/b&gt;", report)
        self.assertIn("+50.0 pp", report)
        self.assertNotIn("cdn", report.lower())

    def test_single_run_report_and_json_round_trip(self) -> None:
        result_path = save_json(self.baseline, self.root / "baseline.json")
        report_path = write_html_report(result_path, self.root / "single.html")
        report = report_path.read_text(encoding="utf-8")
        self.assertIn("提示词实验运行报告", report)
        self.assertIn("共同", self.baseline["by_tag"])
        self.assertIn("转义", report)
        self.assertIn("仅为估算", report)

    def test_different_dataset_hash_cannot_be_compared(self) -> None:
        changed = json.loads(json.dumps(self.candidate))
        changed["manifest"]["dataset"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "数据 SHA256 不同"):
            compare_runs(self.baseline, changed)


if __name__ == "__main__":
    unittest.main()
