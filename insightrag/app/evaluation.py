"""金标检索评测：Recall@k、MRR、引用编号合法率和端到端延迟。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import time
from typing import Iterable, Sequence

from .llm import answer_question, extract_citations
from .retrieval import HybridRetriever, SearchResult


@dataclass(frozen=True, slots=True)
class GoldCase:
    question: str
    expected_sources: tuple[str, ...]

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
        return cls(question.strip(), tuple(dict.fromkeys(expected)))

    def to_dict(self) -> dict[str, object]:
        return {
            "question": self.question,
            "expected_sources": list(self.expected_sources),
        }


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
        valid, citations = citation_counts(str(answer["answer"]), results)
        total_valid += valid
        total_citations += citations
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
                "latency_ms": round(elapsed_ms, 3),
                "answer_mode": answer["mode"],
            }
        )

    count = len(cases)
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
