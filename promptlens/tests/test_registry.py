from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

from promptlens.models import PromptRenderError
from promptlens.registry import PromptRegistry


class RegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = PromptRegistry(self.root / "registry")

    def test_registration_is_idempotent_and_version_is_frozen(self) -> None:
        first = self.registry.register("classifier", "输入：${input}", "v1")
        second = self.registry.register("classifier", "输入：${input}", "v1")
        self.assertEqual(first, second)
        self.assertEqual(len(self.registry.list()), 1)
        self.assertTrue(first.version_id.startswith("p_"))
        with self.assertRaises(FrozenInstanceError):
            first.template = "篡改"

    def test_same_id_with_different_content_is_rejected(self) -> None:
        original = self.registry.register("classifier", "原始 ${input}")
        with self.assertRaisesRegex(ValueError, "拒绝将不同内容"):
            self.registry.register(
                "classifier",
                "不同 ${input}",
                expected_version_id=original.version_id,
            )

    def test_tampered_persisted_content_is_rejected(self) -> None:
        version = self.registry.register("classifier", "原始 ${input}")
        path = self.root / "registry" / f"{version.version_id}.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["template"] = "被篡改 ${input}"
        path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "内容与 ID 不符"):
            self.registry.get(version.version_id)

    def test_missing_variable_has_clear_error(self) -> None:
        version = self.registry.register("classifier", "$input 属于 $category")
        with self.assertRaisesRegex(PromptRenderError, "缺少模板变量：category") as caught:
            version.render({"input": "文本"})
        self.assertNotIn("category", caught.exception.args[0].split("：", 1)[0])

    def test_object_input_and_literal_substitution_without_eval(self) -> None:
        version = self.registry.register("classifier", "地区=$region；完整输入=${input}")
        rendered = version.render({"region": "华东", "value": "__import__('os').system('bad')"})
        self.assertIn("地区=华东", rendered)
        self.assertIn("__import__('os').system('bad')", rendered)
        self.assertIn('"region":"华东"', rendered)

    def test_invalid_template_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "模板无效"):
            self.registry.register("bad", "非法 $ 占位")


if __name__ == "__main__":
    unittest.main()
