"""金标检索评测：Recall@k、MRR、引用编号合法率、人工忠实度与端到端延迟。"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Iterable, Sequence

from .llm import answer_question, extract_citations
from .retrieval import HybridRetriever, SearchResult


_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")


@dataclass(frozen=True, slots=True)
class FaithfulnessAnnotation:
    """人工对离线摘要快照的忠实度判定；可选绑定回答指纹防止标签漂移。"""

    faithful: bool
    answer_sha256: str | None = None

    @classmethod
    def from_dict(cls, data: object) -> "FaithfulnessAnnotation":
        if not isinstance(data, dict):
            raise ValueError("faithfulness 必须是对象")
        faithful = data.get("faithful")
        if not isinstance(faithful, bool):
            raise ValueError("faithfulness.faithful 必须是布尔值")
        digest = data.get("answer_sha256")
        if digest is not None:
            if not isinstance(digest, str) or not _SHA256_RE.fullmatch(digest):
                raise ValueError(
                    "faithfulness.answer_sha256 必须是 64 位十六进制字符串"
                )
            digest = digest.lower()
        return cls(faithful, digest)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"faithful": self.faithful}
        if self.answer_sha256 is not None:
            payload["answer_sha256"] = self.answer_sha256
        return payload


def answer_sha256(text: str) -> str:
    """计算回答文本指纹，用于判断人工标注是否仍对应当前离线摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class GoldCase:
    question: str
    expected_sources: tuple[str, ...]
    faithfulness: FaithfulnessAnnotation | None = None

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> "GoldCase":
        question = data.get("question")
        expected = data.get("expected_sources", data.get("source"))
        if isinstance(expected, str):
            expected = [expected]
        if not isinstance(question, str) or not question.strip():
            raise ValueError("金标 question 必须是非空字符串")
        if not isinstance(expected, (list, tuple)) or not expected:
            raise ValueError("金标 expected_sources 必须是非空数组")
        if not all(isinstance(source, str) and source for source in expected):
            raise ValueError("expected_sources 中的 source 标识必须是非空字符串")
        raw_faithfulness = data.get("faithfulness")
        annotation = (
            None
            if raw_faithfulness is None
            else FaithfulnessAnnotation.from_dict(raw_faithfulness)
        )
        return cls(
            question.strip(),
            tuple(dict.fromkeys(expected)),
            annotation,
        )

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "question": self.question,
            "expected_sources": list(self.expected_sources),
        }
        if self.faithfulness is not None:
            payload["faithfulness"] = self.faithfulness.to_dict()
        return payload


def load_gold_cases(path: str | Path) -> list[GoldCase]:
    cases: list[GoldCase] = []
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
                cases.append(GoldCase.from_dict(data))
            except (json.JSONDecodeError, ValueError, TypeError) as exc:
                raise ValueError(f"金标第 {line_number} 行无效：{exc}") from exc
    if not cases:
        raise ValueError("金标文件没有评测样本")
    return cases


def recall_at_k(
    retrieved_sources: Sequence[str], expected_sources: Iterable[str], k: int
) -> float:
    expected = set(expected_sources)
    if not expected:
        raise ValueError("expected_sources 不能为空")
    retrieved = set(retrieved_sources[:k])
    return len(retrieved & expected) / len(expected)


def reciprocal_rank(
    retrieved_sources: Sequence[str], expected_sources: Iterable[str]
) -> float:
    expected = set(expected_sources)
    for rank, source in enumerate(retrieved_sources, start=1):
        if source in expected:
            return 1.0 / rank
    return 0.0


def citation_counts(answer: str, results: Sequence[SearchResult]) -> tuple[int, int]:
    citations = extract_citations(answer)
    valid = {item.citation for item in results}
    return sum(citation in valid for citation in citations), len(citations)


def faithfulness_for_answer(
    case: GoldCase, answer_text: str
) -> tuple[bool | None, str]:
    """解析人工标注：无标注记 unlabeled；指纹不匹配记 stale，均不计分。"""
    annotation = case.faithfulness
    if annotation is None:
        return None, "unlabeled"
    if (
        annotation.answer_sha256 is not None
        and annotation.answer_sha256 != answer_sha256(answer_text)
    ):
        return None, "stale"
    return annotation.faithful, "labeled"


def evaluate_cases(
    cases: Sequence[GoldCase],
    retriever: HybridRetriever,
    top_k: int = 5,
    output_path: str | Path | None = None,
) -> dict[str, object]:
    if not cases:
        raise ValueError("至少需要一个金标样本")
    if top_k <= 0:
        raise ValueError("top_k 必须大于 0")

    details: list[dict[str, object]] = []
    total_valid = 0
    total_citations = 0
    for case in cases:
        started = time.perf_counter()
        results = retriever.search(case.question, top_k=top_k)
        # 固定离线评测，避免外部服务波动影响金标指标和演示成本。
        answer = answer_question(case.question, results, allow_online=False)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        # 金标以 source 为单位，所以同一文档的多个块只保留首次名次。
        retrieved_sources = list(dict.fromkeys(item.source for item in results))
        answer_text = str(answer["answer"])
        valid, citations = citation_counts(answer_text, results)
        total_valid += valid
        total_citations += citations
        faithfulness, faithfulness_status = faithfulness_for_answer(
            case, answer_text
        )
        details.append(
            {
                "question": case.question,
                "expected_sources": list(case.expected_sources),
                "retrieved_sources": retrieved_sources,
                "recall_at_k": recall_at_k(
                    retrieved_sources, case.expected_sources, top_k
                ),
                "reciprocal_rank": reciprocal_rank(
                    retrieved_sources, case.expected_sources
                ),
                "valid_citation_ids": valid,
                "total_citation_ids": citations,
                "citation_id_valid_rate": (
                    round(valid / citations, 6) if citations else None
                ),
                "faithfulness": faithfulness,
                "faithfulness_status": faithfulness_status,
                "latency_ms": round(elapsed_ms, 3),
                "answer_mode": answer["mode"],
            }
        )

    count = len(cases)
    labeled = [
        item for item in details if item["faithfulness_status"] == "labeled"
    ]
    unlabeled = sum(
        1 for item in details if item["faithfulness_status"] == "unlabeled"
    )
    stale = sum(1 for item in details if item["faithfulness_status"] == "stale")
    report: dict[str, object] = {
        "cases": count,
        "k": top_k,
        "recall_at_k": round(
            sum(float(item["recall_at_k"]) for item in details) / count, 6
        ),
        "mrr": round(
            sum(float(item["reciprocal_rank"]) for item in details) / count, 6
        ),
        # 只校验编号是否指向本次检索；零引用没有可评分对象，记 N/A（JSON null）。
        "citation_id_valid_rate": (
            round(total_valid / total_citations, 6) if total_citations else None
        ),
        "citation_metric_note": (
            "仅校验引用编号合法性，不代表语义忠实度；无引用时记 N/A。"
        ),
        # 人工忠实度只统计与当前回答指纹一致的人工标注；未标注或失效记 N/A。
        "semantic_faithfulness_rate": (
            round(
                sum(1 for item in labeled if item["faithfulness"]) / len(labeled),
                6,
            )
            if labeled
            else None
        ),
        "faithfulness_labeled_cases": len(labeled),
        "faithfulness_unlabeled_cases": unlabeled,
        "faithfulness_stale_cases": stale,
        "faithfulness_metric_note": (
            "人工忠实度仅针对确定性离线摘要快照，按回答指纹匹配计入；"
            "未标注或指纹失效记 N/A，不按 1.0 计算，也不代表在线生成或生产语义忠实度。"
        ),
        "average_latency_ms": round(
            sum(float(item["latency_ms"]) for item in details) / count, 3
        ),
        "details": details,
    }
    if output_path is not None:
        save_evaluation(report, output_path)
    return report


def save_evaluation(report: dict[str, object], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
