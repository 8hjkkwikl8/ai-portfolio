"""透明、确定性的输出指标。"""

from __future__ import annotations

import re
from typing import Any


def exact_match(output: str, expected: str) -> float:
    """去除首尾空白后完全一致记 1，否则记 0。"""
    return float(output.strip() == expected.strip())


def contains(output: str, expected: str) -> float:
    """期望文本（去除首尾空白）是输出子串时记 1。"""
    return float(expected.strip() in output)


def regex_match(output: str, pattern: str) -> float:
    """正则能搜索到输出时记 1；使用 MULTILINE，不启用危险扩展。"""
    return float(re.search(pattern, output, flags=re.MULTILINE) is not None)


def evaluate_item(output: str | None, expected: str, pattern: str | None) -> dict[str, float | None]:
    """失败输出按 0 计；未提供 pattern 时 regex 为 None，不进入其分母。"""
    if output is None:
        return {
            "exact_match": 0.0,
            "contains": 0.0,
            "regex": 0.0 if pattern is not None else None,
        }
    return {
        "exact_match": exact_match(output, expected),
        "contains": contains(output, expected),
        "regex": regex_match(output, pattern) if pattern is not None else None,
    }


def summarize_metrics(records: list[dict[str, Any]]) -> dict[str, dict[str, float | int | None]]:
    """以全部适用样本为分母，失败不会从指标中消失。"""
    summary: dict[str, dict[str, float | int | None]] = {}
    for name in ("exact_match", "contains", "regex"):
        values = [record["metrics"][name] for record in records if record["metrics"][name] is not None]
        correct = sum(values)
        summary[name] = {
            "mean": (correct / len(values)) if values else None,
            "correct": int(correct),
            "evaluated": len(values),
        }
    return summary


def summarize_by_tag(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    tags = sorted({tag for record in records for tag in record.get("tags", [])})
    result: dict[str, dict[str, Any]] = {}
    for tag in tags:
        subset = [record for record in records if tag in record.get("tags", [])]
        result[tag] = {
            "count": len(subset),
            "succeeded": sum(record["error"] is None for record in subset),
            "failed": sum(record["error"] is not None for record in subset),
            "metrics": summarize_metrics(subset),
        }
    return result
