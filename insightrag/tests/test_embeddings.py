from __future__ import annotations

import math
import unittest
from typing import Sequence
from unittest import mock

from app.embeddings import (
    EmbeddingConfig,
    EmbeddingError,
    OpenAIEmbeddingVectorizer,
    build_embedding_vectorizer_from_env,
)
from app.retrieval import HybridRetriever, HashedNgramVectorizer
from app.store import KnowledgeStore


class FakeTransport:
    """确定性假嵌入服务：按输入文本长度生成可预测向量。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, url, payload, headers, timeout):  # type: ignore[no-untyped-def]
        self.calls.append(
            {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
        )
        inputs = payload["input"]
        assert isinstance(inputs, list)
        data = []
        for index, text in enumerate(inputs):
            length = float(len(str(text)) or 1)
            data.append(
                {
                    "index": index,
                    "embedding": [length, 1.0, -1.0],
                }
            )
        # 故意倒序返回，验证按 index 重排。
        return {"data": list(reversed(data))}


def _config(**overrides: object) -> EmbeddingConfig:
    values: dict[str, object] = {
        "base_url": "http://localhost:9999/v1",
        "model": "fake-embedding",
        "api_key": "secret",
        "timeout_seconds": 5.0,
        "batch_size": 64,
    }
    values.update(overrides)
    return EmbeddingConfig(**values)  # type: ignore[arg-type]


class EmbeddingConfigTests(unittest.TestCase):
    def test_unconfigured_env_never_creates_a_client(self) -> None:
        self.assertFalse(EmbeddingConfig.from_env({}).configured)
        self.assertIsNone(build_embedding_vectorizer_from_env({}))
        only_url = {"INSIGHTRAG_EMBEDDING_BASE_URL": "http://localhost/v1"}
        self.assertIsNone(build_embedding_vectorizer_from_env(only_url))
        only_model = {"INSIGHTRAG_EMBEDDING_MODEL": "text-embedding-3-small"}
        self.assertIsNone(build_embedding_vectorizer_from_env(only_model))

    def test_factory_never_calls_network_when_unconfigured(self) -> None:
        with mock.patch(
            "app.embeddings.post_json", side_effect=AssertionError("不应联网")
        ):
            self.assertIsNone(build_embedding_vectorizer_from_env({}))

    def test_api_key_falls_back_to_openai_key(self) -> None:
        config = EmbeddingConfig.from_env(
            {
                "INSIGHTRAG_EMBEDDING_BASE_URL": "http://localhost/v1",
                "INSIGHTRAG_EMBEDDING_MODEL": "m",
                "OPENAI_API_KEY": "fallback",
            }
        )
        self.assertTrue(config.configured)
        self.assertEqual(config.api_key, "fallback")


class OpenAIEmbeddingVectorizerTests(unittest.TestCase):
    def test_request_shape_auth_and_normalized_vectors(self) -> None:
        calls: list[dict[str, object]] = []
        embeddings = {"差旅制度": [3.0, 0.0, 0.0], "abc": [0.0, 2.0, 0.0]}

        def transport(url, payload, headers, timeout):  # type: ignore[no-untyped-def]
            calls.append(
                {"url": url, "payload": payload, "headers": headers, "timeout": timeout}
            )
            items = [
                {"index": index, "embedding": embeddings[text]}
                for index, text in enumerate(payload["input"])
            ]
            # 故意倒序返回，验证按 index 重排。
            return {"data": list(reversed(items))}

        vectorizer = OpenAIEmbeddingVectorizer(_config(), transport=transport)
        vectors = vectorizer.transform_many(["差旅制度", "abc"])

        self.assertEqual(len(calls), 1)
        call = calls[0]
        self.assertEqual(call["url"], "http://localhost:9999/v1/embeddings")
        self.assertEqual(call["headers"]["Authorization"], "Bearer secret")
        self.assertEqual(call["payload"]["model"], "fake-embedding")
        self.assertEqual(call["payload"]["input"], ["差旅制度", "abc"])
        self.assertEqual(call["timeout"], 5.0)

        self.assertEqual(len(vectors), 2)
        for vector in vectors:
            norm = math.sqrt(sum(value * value for value in vector.values()))
            self.assertAlmostEqual(norm, 1.0, places=9)
        # 重排后第一条对应“差旅制度”，第二条对应“abc”。
        self.assertAlmostEqual(vectors[0].get(0, 0.0), 1.0)
        self.assertAlmostEqual(vectors[1].get(1, 0.0), 1.0)

    def test_batches_large_inputs(self) -> None:
        transport = FakeTransport()
        vectorizer = OpenAIEmbeddingVectorizer(
            _config(batch_size=64), transport=transport
        )
        texts = [f"文本-{index}" for index in range(130)]
        vectors = vectorizer.transform_many(texts)

        self.assertEqual(len(vectors), 130)
        self.assertEqual([len(call["payload"]["input"]) for call in transport.calls], [64, 64, 2])
        self.assertEqual(transport.calls[1]["payload"]["input"][0], "文本-64")
        self.assertEqual(transport.calls[2]["payload"]["input"][-1], "文本-129")

    def test_empty_input_makes_no_request(self) -> None:
        transport = FakeTransport()
        vectorizer = OpenAIEmbeddingVectorizer(_config(), transport=transport)
        self.assertEqual(vectorizer.transform_many([]), [])
        self.assertEqual(transport.calls, [])

    def test_rejects_count_mismatch(self) -> None:
        vectorizer = OpenAIEmbeddingVectorizer(
            _config(), transport=lambda *_: {"data": [{"embedding": [1.0]}]}
        )
        with self.assertRaises(EmbeddingError):
            vectorizer.transform_many(["a", "b"])

    def test_rejects_non_numeric_and_nan(self) -> None:
        for bad in ([True], ["x"], [float("nan")], []):
            vectorizer = OpenAIEmbeddingVectorizer(
                _config(), transport=lambda *_, bad=bad: {"data": [{"embedding": bad}]}
            )
            with self.assertRaises(EmbeddingError):
                vectorizer.transform_many(["a"])

    def test_constructor_requires_config(self) -> None:
        with self.assertRaises(ValueError):
            OpenAIEmbeddingVectorizer(EmbeddingConfig())


class CountingVectorizer:
    name = "counting"

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    def transform_many(self, texts: Sequence[str]) -> list[dict[int, float]]:
        self.batches.append(list(texts))
        return [
            {0: 1.0} if "差旅" in text or "住宿" in text else {0: 0.0}
            for text in texts
        ]


class RetrieverVectorizerTests(unittest.TestCase):
    def test_custom_vectorizer_is_used_and_cached(self) -> None:
        store = KnowledgeStore(":memory:")
        try:
            store.ingest_document(
                "travel.txt", "差旅制度。住宿限额为每晚五百元。", chunk_size=12, overlap=2
            )
            store.ingest_document(
                "security.txt", "安全规范。泄露后三十分钟内报告。", chunk_size=12, overlap=2
            )
            vectorizer = CountingVectorizer()
            retriever = HybridRetriever(store, vectorizer=vectorizer)
            chunk_count = len(store.all_chunks())

            first = retriever.search("差旅住宿限额", top_k=3)
            self.assertTrue(first)
            self.assertEqual(len(vectorizer.batches), 2)
            self.assertEqual(len(vectorizer.batches[1]), chunk_count)
            self.assertGreater(first[0].vector_score, 0)

            second = retriever.search("差旅住宿限额", top_k=3)
            self.assertEqual(len(vectorizer.batches), 3)
            self.assertEqual(len(vectorizer.batches[2]), 1)
            self.assertEqual(
                [item.to_dict() for item in first],
                [item.to_dict() for item in second],
            )
        finally:
            store.close()

    def test_default_vectorizer_is_still_hash_based(self) -> None:
        store = KnowledgeStore(":memory:")
        try:
            retriever = HybridRetriever(store)
            self.assertIsInstance(retriever.vectorizer, HashedNgramVectorizer)
            self.assertTrue(retriever.vectorizer.name.startswith("hash-ngram"))
        finally:
            store.close()


if __name__ == "__main__":
    unittest.main()
