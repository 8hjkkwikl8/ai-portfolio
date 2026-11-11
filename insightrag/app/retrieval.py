"""BM25 与轻量哈希 n-gram 向量的确定性混合检索。"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import math
import re
from typing import Iterable

from .store import KnowledgeStore, StoredChunk


_TOKEN_RE = re.compile(
    r"[A-Za-z]+(?:['-][A-Za-z]+)*|[0-9]+(?:\.[0-9]+)?|[\u3400-\u4dbf\u4e00-\u9fff]+"
)


def tokenize(text: str) -> list[str]:
    """英文/数字按词，中文按单字与相邻二元组，统一小写并加命名空间。"""
    tokens: list[str] = []
    for match in _TOKEN_RE.finditer(text.lower()):
        value = match.group(0)
        if value[0].isascii():
            tokens.append(f"en:{value}")
            continue
        chars = list(value)
        tokens.extend(f"zh:{char}" for char in chars)
        tokens.extend(
            f"zh2:{value[index:index + 2]}" for index in range(len(value) - 1)
        )
    return tokens


class HashedNgramVectorizer:
    """特征哈希稀疏向量，不是神经嵌入，也不宣称具备语义理解能力。"""

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 16:
            raise ValueError("哈希向量维度至少为 16")
        self.dimensions = dimensions

    def transform(self, text: str) -> dict[int, float]:
        tokens = tokenize(text)
        features = [f"1:{token}" for token in tokens]
        features.extend(
            f"2:{left}\x1f{right}" for left, right in zip(tokens, tokens[1:])
        )
        vector: dict[int, float] = {}
        for feature in features:
            digest = hashlib.blake2b(
                feature.encode("utf-8"), digest_size=8, person=b"InsightRAG"
            ).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            sign = -1.0 if digest[4] & 1 else 1.0
            vector[index] = vector.get(index, 0.0) + sign
        norm = math.sqrt(sum(value * value for value in vector.values()))
        if norm:
            vector = {index: value / norm for index, value in vector.items()}
        return vector

    @staticmethod
    def cosine(left: dict[int, float], right: dict[int, float]) -> float:
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        return sum(value * right.get(index, 0.0) for index, value in left.items())


class BM25:
    """小型语料即时构建的 Okapi BM25，避免引入外部索引服务。"""

    def __init__(self, documents: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.documents = documents
        self.k1 = k1
        self.b = b
        self.term_frequencies = [Counter(document) for document in documents]
        self.document_frequencies: Counter[str] = Counter()
        for document in documents:
            self.document_frequencies.update(set(document))
        self.average_length = (
            sum(len(document) for document in documents) / len(documents)
            if documents
            else 0.0
        )

    def scores(self, query_tokens: Iterable[str]) -> list[float]:
        count = len(self.documents)
        if not count or self.average_length == 0:
            return [0.0] * count
        output: list[float] = []
        for index, frequencies in enumerate(self.term_frequencies):
            document_length = len(self.documents[index])
            score = 0.0
            for term in set(query_tokens):
                frequency = frequencies.get(term, 0)
                if not frequency:
                    continue
                df = self.document_frequencies[term]
                idf = math.log(1.0 + (count - df + 0.5) / (df + 0.5))
                denominator = frequency + self.k1 * (
                    1.0 - self.b + self.b * document_length / self.average_length
                )
                score += idf * frequency * (self.k1 + 1.0) / denominator
            output.append(score)
        return output


@dataclass(frozen=True, slots=True)
class SearchResult:
    source: str
    chunk_id: str
    text: str
    snippet: str
    start: int
    end: int
    score: float
    bm25_score: float
    vector_score: float
    bm25_rank: int | None
    vector_rank: int | None
    citation: int
    ordinal: int = 0
    key: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "citation": self.citation,
            "source": self.source,
            "chunk_id": self.chunk_id,
            "start": self.start,
            "end": self.end,
            "text": self.text,
            "snippet": self.snippet,
            "score": round(self.score, 10),
            "bm25_score": round(self.bm25_score, 8),
            "vector_score": round(self.vector_score, 8),
            "bm25_rank": self.bm25_rank,
            "vector_rank": self.vector_rank,
        }


def make_snippet(text: str, limit: int = 220) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


class HybridRetriever:
    def __init__(
        self,
        store: KnowledgeStore,
        rrf_k: int = 60,
        hash_dimensions: int = 256,
        candidate_pool: int = 40,
    ) -> None:
        if rrf_k <= 0 or candidate_pool <= 0:
            raise ValueError("rrf_k 和 candidate_pool 必须大于 0")
        self.store = store
        self.rrf_k = rrf_k
        self.vectorizer = HashedNgramVectorizer(hash_dimensions)
        self.candidate_pool = candidate_pool

    def search(self, query: str, top_k: int = 5) -> list[SearchResult]:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("查询不能为空")
        if top_k <= 0:
            raise ValueError("top_k 必须大于 0")
        chunks = self.store.all_chunks()
        query_tokens = tokenize(query)
        if not chunks or not query_tokens:
            return []

        documents = [tokenize(chunk.text) for chunk in chunks]
        bm25_scores = BM25(documents).scores(query_tokens)
        query_vector = self.vectorizer.transform(query)
        vector_scores = [
            self.vectorizer.cosine(query_vector, self.vectorizer.transform(chunk.text))
            for chunk in chunks
        ]
        pool_size = min(len(chunks), max(self.candidate_pool, top_k * 4))

        def ranked_indexes(scores: list[float]) -> list[int]:
            positive = [index for index, score in enumerate(scores) if score > 1e-12]
            positive.sort(
                key=lambda index: (
                    -scores[index],
                    chunks[index].source,
                    chunks[index].ordinal,
                    chunks[index].chunk_id,
                )
            )
            return positive[:pool_size]

        bm25_indexes = ranked_indexes(bm25_scores)
        vector_indexes = ranked_indexes(vector_scores)
        fused: dict[int, float] = {}
        bm25_ranks: dict[int, int] = {}
        vector_ranks: dict[int, int] = {}

        # RRF 只使用名次：1/(k+rank)。这样无需假设 BM25 与余弦分数同尺度，
        # 某一通道异常大的原始分也不会完全压制另一通道。
        for rank, index in enumerate(bm25_indexes, start=1):
            bm25_ranks[index] = rank
            fused[index] = fused.get(index, 0.0) + 1.0 / (self.rrf_k + rank)
        for rank, index in enumerate(vector_indexes, start=1):
            vector_ranks[index] = rank
            fused[index] = fused.get(index, 0.0) + 1.0 / (self.rrf_k + rank)

        ordered = sorted(
            fused,
            key=lambda index: (
                -fused[index],
                -bm25_scores[index],
                -vector_scores[index],
                chunks[index].source,
                chunks[index].ordinal,
                chunks[index].chunk_id,
            ),
        )[:top_k]
        return [
            self._make_result(
                chunk=chunks[index],
                citation=citation,
                score=fused[index],
                bm25_score=bm25_scores[index],
                vector_score=vector_scores[index],
                bm25_rank=bm25_ranks.get(index),
                vector_rank=vector_ranks.get(index),
            )
            for citation, index in enumerate(ordered, start=1)
        ]

    @staticmethod
    def _make_result(
        chunk: StoredChunk,
        citation: int,
        score: float,
        bm25_score: float,
        vector_score: float,
        bm25_rank: int | None,
        vector_rank: int | None,
    ) -> SearchResult:
        return SearchResult(
            source=chunk.source,
            chunk_id=chunk.chunk_id,
            text=chunk.text,
            snippet=make_snippet(chunk.text),
            start=chunk.start,
            end=chunk.end,
            score=score,
            bm25_score=bm25_score,
            vector_score=vector_score,
            bm25_rank=bm25_rank,
            vector_rank=vector_rank,
            citation=citation,
            ordinal=chunk.ordinal,
            key=chunk.key,
        )
