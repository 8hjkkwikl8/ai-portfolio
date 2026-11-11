"""基于内容寻址的不可变提示词注册表。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .models import PromptVersion


class PromptRegistry:
    """每个版本独立存储；不存在指向可变内容的 latest 别名。"""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, version_id: str) -> Path:
        if not version_id.startswith("p_") or len(version_id) != 66:
            raise ValueError(f"无效的提示词版本 ID：{version_id!r}")
        return self.root / f"{version_id}.json"

    def register(
        self,
        name: str,
        template: str,
        description: str = "",
        *,
        expected_version_id: str | None = None,
    ) -> PromptVersion:
        version = PromptVersion.create(name, template, description)
        if expected_version_id is not None and version.version_id != expected_version_id:
            raise ValueError(
                "拒绝将不同内容写入同一版本 ID："
                f"指定 {expected_version_id}，内容实际生成 {version.version_id}"
            )
        path = self._path(version.version_id)
        if path.exists():
            existing = self.get(version.version_id)
            if existing.to_dict() != version.to_dict():
                raise ValueError(f"版本 ID {version.version_id} 已对应不同内容，拒绝覆盖")
            return existing
        payload = json.dumps(version.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        fd, temporary_name = tempfile.mkstemp(prefix=".prompt-", suffix=".tmp", dir=self.root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
            os.replace(temporary_name, path)
        finally:
            if os.path.exists(temporary_name):
                os.unlink(temporary_name)
        return version

    def get(self, version_id: str) -> PromptVersion:
        path = self._path(version_id)
        if not path.is_file():
            raise KeyError(f"找不到提示词版本：{version_id}（{path}）")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            version = PromptVersion.from_dict(raw)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"提示词版本 {version_id} 已损坏或内容与 ID 不符：{exc}") from exc
        if version.version_id != version_id:
            raise ValueError(f"文件名版本 ID 与内容版本 ID 不一致：{version_id}")
        return version

    def list(self) -> list[PromptVersion]:
        versions = [self.get(path.stem) for path in self.root.glob("p_*.json")]
        return sorted(versions, key=lambda item: (item.name, item.version_id))
