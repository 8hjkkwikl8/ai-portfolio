from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from promptlens.metrics import contains, evaluate_item, exact_match, regex_match
from promptlens.models import DatasetValidationError, load_dataset


class DatasetAndMetricTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def write_dataset(self, rows: list[object], name: str = "data.jsonl") -> Path:
        path = self.root / name
        path.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        return path

    def test_duplicate_id_is_rejected(self) -> None:
        path = self.write_dataset(
            [
                {"id": "same", "input": "a", "expected": "a"},
                {"id": "same", "input": "b", "expected": "b"},
            ]
        )
        with self.assertRaisesRegex(DatasetValidationError, "重复数据 id"):
            load_dataset(path)

    def test_missing_required_field_is_rejected(self) -> None:
        path = self.write_dataset([{"id": "missing", "input": "a"}])
        with self.assertRaisesRegex(DatasetValidationError, "缺少字段：expected"):
            load_dataset(path)

    def test_object_input_tags_and_pattern_are_loaded(self) -> None:
        path = self.write_dataset(
            [
                {
                    "id": "object",
                    "input": {"text": "反馈", "region": "华东"},
                    "expected": "投诉",
                    "tags": ["tag-b", "tag-a", "tag-a"],
                    "pattern": "^投诉$",
                }
            ]
        )
        item = load_dataset(path)[0]
        self.assertEqual(item.input["region"], "华东")
        self.assertEqual(item.tags, ("tag-a", "tag-b"))
        self.assertEqual(item.pattern, "^投诉$")

    def test_invalid_regex_and_input_type_are_rejected(self) -> None:
        bad_regex = self.write_dataset(
            [{"id": "regex", "input": "a", "expected": "a", "pattern": "["}],
            "regex.jsonl",
        )
        with self.assertRaisesRegex(DatasetValidationError, "pattern 无效"):
            load_dataset(bad_regex)
        bad_input = self.write_dataset(
            [{"id": "list", "input": ["not", "allowed"], "expected": "a"}],
            "input.jsonl",
        )
        with self.assertRaisesRegex(DatasetValidationError, "字符串或对象"):
            load_dataset(bad_input)

    def test_all_metrics(self) -> None:
        self.assertEqual(exact_match(" 投诉 \n", "投诉"), 1.0)
        self.assertEqual(exact_match("投诉：物流", "投诉"), 0.0)
        self.assertEqual(contains("类别：投诉；理由：物流", "投诉"), 1.0)
        self.assertEqual(contains("类别：咨询", "投诉"), 0.0)
        self.assertEqual(regex_match("第一行\n投诉｜物流", "^投诉｜"), 1.0)
        self.assertEqual(regex_match("咨询｜物流", "^投诉｜"), 0.0)

    def test_failed_item_scores_zero_and_missing_pattern_is_not_scored(self) -> None:
        self.assertEqual(
            evaluate_item(None, "期望", None),
            {"exact_match": 0.0, "contains": 0.0, "regex": None},
        )
        self.assertEqual(evaluate_item(None, "期望", ".*")["regex"], 0.0)


if __name__ == "__main__":
    unittest.main()
