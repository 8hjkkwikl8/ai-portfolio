"""本地 JSONL 查询审计：字段白名单、SQL 哈希与常见密钥脱敏。"""

from __future__ import annotations

import json
import os
import re
import threading
import warnings
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AUDIT_LOG_PATH = PROJECT_ROOT / "artifacts" / "query_audit.jsonl"
_DISABLED_VALUES = {"0", "disabled", "false", "none", "off"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)((?:[a-z0-9]+[_-])*(?:api[_-]?key|authorization|access[_-]?token|"
    r"refresh[_-]?token|token|password|secret)|密码|密钥|令牌)"
    r"\s*[:：=＝]\s*(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,，;；]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_COMMON_API_KEY = re.compile(r"\b(?:sk|pk)-[a-z0-9_-]{8,}\b", re.IGNORECASE)

# 只序列化明确允许的字段，避免未来调用方误把 params、SQL 或环境配置写入日志。
AUDIT_FIELDS = (
    "schema_version",
    "timestamp",
    "question",
    "mode",
    "status",
    "guard_result",
    "sql_sha256",
    "guarded_sql_sha256",
    "row_count",
    "duration_ms",
    "error_type",
)


def redact_question(question: str) -> str:
    """保留问法可读性，同时遮盖常见密钥赋值、Bearer 与 API Key 形式。"""

    redacted = question[:500]
    redacted = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", redacted)
    redacted = _BEARER_TOKEN.sub("Bearer <redacted>", redacted)
    return _COMMON_API_KEY.sub("<redacted-api-key>", redacted)


def sql_sha256(sql: str) -> str:
    return sha256(sql.encode("utf-8")).hexdigest()


def new_query_event(question: str, mode: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "question": redact_question(question),
        "mode": mode,
        "status": "started",
        "guard_result": "not_run",
        "sql_sha256": None,
        "guarded_sql_sha256": None,
        "row_count": None,
        "duration_ms": None,
        "error_type": None,
    }


def resolve_audit_log_path(path: str | os.PathLike[str] | None = None) -> Path | None:
    configured: str | os.PathLike[str] = path or os.getenv("DATACHAT_AUDIT_LOG") or DEFAULT_AUDIT_LOG_PATH
    if isinstance(configured, str) and configured.strip().casefold() in _DISABLED_VALUES:
        return None
    return Path(configured).expanduser().resolve()


class QueryAuditLogger:
    """进程内串行追加 JSONL；这是本地演示审计，不是防篡改日志系统。"""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser().resolve()
        self._lock = threading.Lock()

    def record(self, event: Mapping[str, Any]) -> None:
        safe_event = {field: event.get(field) for field in AUDIT_FIELDS}
        safe_event["question"] = redact_question(str(safe_event["question"] or ""))
        line = json.dumps(safe_event, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        try:
            with self._lock:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(line + "\n")
        except OSError:
            # 本地演示采用 best-effort 日志；告警不包含路径、SQL、参数或异常原文。
            warnings.warn(
                "DataChat BI 审计日志写入失败；本次只读查询结果不受影响。",
                RuntimeWarning,
                stacklevel=2,
            )
