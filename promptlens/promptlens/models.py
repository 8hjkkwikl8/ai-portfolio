"""核心数据模型、严格模板渲染与 JSONL 数据集校验。"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from string import Template
from typing import Any, Mapping


class PromptRenderError(ValueError):
    """模板无效或缺少渲染变量。"""


class DatasetValidationError(ValueError):
    """数据集不符合 JSONL 架构。"""


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def prompt_version_id(name: str, template: str, description: str = "") -> str:
    """以全部注册内容计算不可变版本 ID。"""
    content = {"description": description, "name": name, "template": template}
    return "p_" + hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class PromptVersion:
    name: str
    template: str
    description: str
    version_id: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("提示词名称不能为空")
        expected_id = prompt_version_id(self.name, self.template, self.description)
        if self.version_id != expected_id:
            raise ValueError(
                f"版本 ID 与内容不匹配：期望 {expected_id}，实际 {self.version_id}；拒绝复用该 ID"
            )
        try:
            compiled = Template(self.template)
            identifiers = compiled.get_identifiers()
            # get_identifiers() 会忽略部分非法 $；用空值完整替换以触发严格语法校验。
            compiled.substitute({identifier: "" for identifier in identifiers})
        except ValueError as exc:
            raise ValueError(f"提示词模板无效：{exc}；字面量 $ 请写成 $$") from exc

    @classmethod
    def create(cls, name: str, template: str, description: str = "") -> "PromptVersion":
        if not isinstance(template, str):
            raise TypeError("提示词模板必须是字符串")
        if not isinstance(description, str):
            raise TypeError("提示词描述必须是字符串")
        return cls(
            name=name,
            template=template,
            description=description,
            version_id=prompt_version_id(name, template, description),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PromptVersion":
        return cls(
            name=raw["name"],
            template=raw["template"],
            description=raw.get("description", ""),
            version_id=raw["version_id"],
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "version_id": self.version_id,
            "name": self.name,
            "description": self.description,
            "template": self.template,
        }

    def render(self, variables: Mapping[str, Any] | str) -> str:
        """使用 string.Template 严格渲染，不执行任何表达式。"""
        if isinstance(variables, str):
            values: dict[str, Any] = {"input": variables}
        elif isinstance(variables, Mapping):
            values = dict(variables)
            values["input"] = _canonical_json(variables)
        else:
            raise PromptRenderError("模板变量必须是字符串或对象")
        try:
            identifiers = set(Template(self.template).get_identifiers())
        except ValueError as exc:
            raise PromptRenderError(f"提示词模板无效：{exc}") from exc
        missing = sorted(identifiers - values.keys())
        if missing:
            raise PromptRenderError("缺少模板变量：" + "、".join(missing))
        try:
            return Template(self.template).substitute(values)
        except (KeyError, ValueError) as exc:
            raise PromptRenderError(f"提示词渲染失败：{exc}") from exc


@dataclass(frozen=True, slots=True)
class DatasetItem:
    id: str
    input: str | dict[str, Any]
    expected: str
    tags: tuple[str, ...] = ()
    pattern: str | None = None

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], location: str = "数据行") -> "DatasetItem":
        missing = [key for key in ("id", "input", "expected") if key not in raw]
        if missing:
            raise DatasetValidationError(f"{location} 缺少字段：{', '.join(missing)}")
        item_id = raw["id"]
        item_input = raw["input"]
        expected = raw["expected"]
        if not isinstance(item_id, str) or not item_id.strip():
            raise DatasetValidationError(f"{location} 的 id 必须是非空字符串")
        if not isinstance(item_input, (str, dict)):
            raise DatasetValidationError(f"{location}（{item_id}）的 input 必须是字符串或对象")
        if not isinstance(expected, str):
            raise DatasetValidationError(f"{location}（{item_id}）的 expected 必须是字符串")
        raw_tags = raw.get("tags", [])
        if not isinstance(raw_tags, list) or any(not isinstance(tag, str) or not tag for tag in raw_tags):
            raise DatasetValidationError(f"{location}（{item_id}）的 tags 必须是非空字符串数组")
        pattern = raw.get("pattern")
        if pattern is not None:
            if not isinstance(pattern, str):
                raise DatasetValidationError(f"{location}（{item_id}）的 pattern 必须是字符串")
            try:
                re.compile(pattern)
            except re.error as exc:
                raise DatasetValidationError(
                    f"{location}（{item_id}）的 regex pattern 无效：{exc}"
                ) from exc
        return cls(
            id=item_id,
            input=item_input,
            expected=expected,
            tags=tuple(sorted(set(raw_tags))),
            pattern=pattern,
        )

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "id": self.id,
            "input": self.input,
            "expected": self.expected,
            "tags": list(self.tags),
        }
        if self.pattern is not None:
            value["pattern"] = self.pattern
        return value


def load_dataset(path: str | Path) -> list[DatasetItem]:
    """加载并完整校验 JSONL；空行允许，重复 ID 立即拒绝。"""
    dataset_path = Path(path)
    try:
        text = dataset_path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DatasetValidationError(f"数据集不是有效 UTF-8：{dataset_path}") from exc
    items: list[DatasetItem] = []
    seen: dict[str, int] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        location = f"{dataset_path} 第 {line_number} 行"
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise DatasetValidationError(f"{location} 不是有效 JSON：{exc.msg}") from exc
        if not isinstance(raw, dict):
            raise DatasetValidationError(f"{location} 必须是 JSON 对象")
        item = DatasetItem.from_dict(raw, location)
        if item.id in seen:
            raise DatasetValidationError(
                f"{location} 出现重复数据 id {item.id!r}（首次位于第 {seen[item.id]} 行）"
            )
        seen[item.id] = line_number
        items.append(item)
    if not items:
        raise DatasetValidationError(f"数据集为空：{dataset_path}")
    return items


def file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
