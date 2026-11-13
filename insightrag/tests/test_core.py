from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.chunking import chunk_text
from app.evaluation import (
    FaithfulnessAnnotation,
    GoldCase,
    answer_sha256,
    evaluate_cases,
    load_gold_cases,
    recall_at_k,
    reciprocal_rank,
)
from app.llm import answer_question, sanitize_citations
from app.retrieval import HybridRetriever, tokenize
from app.store import KnowledgeStore


class ChunkingTests(unittest.TestCase):
    def test_offsets_replay_and_chunk_ids_are_unique(self) -> None:
        content = (
            "公司差旅规范。\n国内出差应提前申请，住宿需要保留发票。"
            "Remote work requires MFA and a managed laptop.\n"
            "发现安全事件应及时报告，并保存相关日志。"
        ) * 5
        chunks = chunk_text(content, "policy.txt", chunk_size=57, overlap=11)
        self.assertGreater(len(chunks), 3)
        self.assertEqual(len({chunk.chunk_id for chunk in chunks}), len(chunks))
        for chunk in chunks:
            self.assertEqual(chunk.text, content[chunk.start:chunk.end])
            self.assertEqual(chunk.source, "policy.txt")
            self.assertGreaterEqual(chunk.start, 0)
            self.assertLessEqual(chunk.end, len(content))
            self.assertGreater(chunk.end, chunk.start)

    def test_chunk_validation(self) -> None:
        self.assertEqual(chunk_text("", "empty.txt"), [])
        with self.assertRaises(ValueError):
            chunk_text("内容", "a.txt", chunk_size=10, overlap=10)
        with self.assertRaises(ValueError):
            chunk_text("内容", "")

    def test_chinese_and_english_tokenization(self) -> None:
        tokens = tokenize("Remote 远程办公 MFA 2.0")
        self.assertIn("en:remote", tokens)
        self.assertIn("en:mfa", tokens)
        self.assertIn("en:2.0", tokens)
        self.assertIn("zh:远", tokens)
        self.assertIn("zh2:远程", tokens)


class StoreAndRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="insightrag-core-")
        self.db_path = Path(self.temp.name) / "test.db"
        self.store = KnowledgeStore(self.db_path)
        self.store.ingest_document(
            "travel.txt",
            "公司差旅制度。\n国内出差住宿限额为每晚五百元，返程后十日内提交报销。"
            "交通优先选择公共交通，票据必须真实完整。",
        )
        self.store.ingest_document(
            "security.txt",
            "信息安全规范。\n发现信息泄露后三十分钟内报告安全值班渠道，并保留日志。"
            "可疑邮件不得点击附件，应通过报告钓鱼按钮提交。",
        )
        self.retriever = HybridRetriever(self.store)

    def tearDown(self) -> None:
        self.store.close()
        self.temp.cleanup()

    def test_duplicate_ingest_replaces_old_chunks(self) -> None:
        original = self.store.get_document("travel.txt")
        self.assertIsNotNone(original)
        for chunk in self.store.all_chunks():
            if chunk.source == "travel.txt":
                self.assertEqual(chunk.text, str(original["content"])[chunk.start:chunk.end])

        result = self.store.ingest_document(
            "travel.txt", "全新差旅规则。住宿限额调整为六百元。", chunk_size=20, overlap=2
        )
        self.assertTrue(result["replaced"])
        self.assertFalse(result["unchanged"])
        self.assertEqual(self.store.stats()["documents"], 2)
        travel_chunks = [
            chunk for chunk in self.store.all_chunks() if chunk.source == "travel.txt"
        ]
        self.assertEqual(len(travel_chunks), result["chunks"])
        self.assertTrue(all("五百元" not in chunk.text for chunk in travel_chunks))
        current = self.store.get_document("travel.txt")
        for chunk in travel_chunks:
            self.assertEqual(chunk.text, str(current["content"])[chunk.start:chunk.end])

        again = self.store.ingest_document(
            "travel.txt", "全新差旅规则。住宿限额调整为六百元。", chunk_size=20, overlap=2
        )
        self.assertTrue(again["unchanged"])
        self.assertEqual(self.store.stats()["documents"], 2)

    def test_hybrid_retrieval_is_deterministic(self) -> None:
        first = self.retriever.search("差旅住宿限额是多少", top_k=4)
        second = self.retriever.search("差旅住宿限额是多少", top_k=4)
        self.assertTrue(first)
        self.assertEqual([item.to_dict() for item in first], [item.to_dict() for item in second])
        self.assertEqual(first[0].source, "travel.txt")
        self.assertEqual([item.citation for item in first], list(range(1, len(first) + 1)))
        self.assertTrue(any(item.bm25_score > 0 for item in first))
        self.assertTrue(any(item.vector_score > 0 for item in first))
        for item in first:
            self.assertIn(item.snippet[:10], item.text.replace("\n", " "))
            self.assertTrue(item.text)

    def test_no_evidence_answer_is_honest(self) -> None:
        results = self.retriever.search("火星殖民船票怎么买", top_k=5)
        answer = answer_question("火星殖民船票怎么买", results)
        self.assertEqual(answer["mode"], "no_evidence")
        self.assertIn("不知道", str(answer["answer"]))
        self.assertEqual(answer["sources"], [])
        empty = answer_question("完全没有库外证据的问题", [])
        self.assertEqual(empty["mode"], "no_evidence")

    def test_invalid_citations_are_removed_and_sources_are_bounded(self) -> None:
        results = self.retriever.search("差旅住宿限额", top_k=3)
        self.assertTrue(results)

        class FakeGenerator:
            def generate(self, question, current_results):
                return (
                    "以下是回答。住宿有明确限额。[1]\n"
                    "无依据句不应保留。伪造引用[99]和[0]也不应保留。"
                )

        answer = answer_question(
            "差旅住宿限额", results, allow_online=True, client=FakeGenerator()
        )
        self.assertEqual(answer["mode"], "online")
        self.assertNotIn("[99]", str(answer["answer"]))
        self.assertNotIn("[0]", str(answer["answer"]))
        self.assertNotIn("以下是回答", str(answer["answer"]))
        self.assertNotIn("无依据句", str(answer["answer"]))
        self.assertNotIn("伪造引用", str(answer["answer"]))
        self.assertIn("住宿有明确限额。[1]", str(answer["answer"]))
        self.assertEqual(len(answer["sources"]), 1)
        self.assertEqual(answer["sources"][0]["source"], results[0].source)
        valid_ids = {item.citation for item in results}
        self.assertTrue(all(item["citation"] in valid_ids for item in answer["sources"]))
        self.assertEqual(sanitize_citations("甲[ 1 ]乙[8]", {1}), "甲[1]乙")
        self.assertEqual(sanitize_citations("甲[1,2]乙[-1]丙[2,99]", {1, 2}), "甲[1][2]乙丙[2]")

        class CitationLessGenerator:
            def generate(self, question, current_results):
                return "没有任何可验证编号的回答[999]。"

        fallback = answer_question(
            "差旅住宿限额", results, allow_online=True, client=CitationLessGenerator()
        )
        self.assertEqual(fallback["mode"], "offline")
        self.assertIn("回退", str(fallback["warning"]))


class EvaluationTests(unittest.TestCase):
    def test_recall_mrr(self) -> None:
        retrieved = ["other.txt", "target.txt", "target.txt", "third.txt"]
        self.assertEqual(recall_at_k(retrieved, ["target.txt"], 1), 0.0)
        self.assertEqual(recall_at_k(retrieved, ["target.txt"], 2), 1.0)
        self.assertEqual(reciprocal_rank(retrieved, ["target.txt"]), 0.5)
        self.assertEqual(reciprocal_rank(retrieved, ["missing.txt"]), 0.0)
        self.assertEqual(
            recall_at_k(["a.txt"], ["a.txt", "b.txt"], 3), 0.5
        )

    def test_sample_gold_evaluation_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory(prefix="insightrag-eval-") as directory:
            with KnowledgeStore(Path(directory) / "eval.db") as store:
                store.ingest_directory(ROOT / "data" / "sample_docs")
                cases = load_gold_cases(ROOT / "data" / "eval.jsonl")
                output = Path(directory) / "metrics.json"
                report = evaluate_cases(
                    cases, HybridRetriever(store), top_k=3, output_path=output
                )
                self.assertGreaterEqual(report["cases"], 5)
                self.assertGreaterEqual(report["recall_at_k"], 0.8)
                self.assertGreater(report["mrr"], 0.5)
                self.assertEqual(report["citation_id_valid_rate"], 1.0)
                self.assertIn("不代表语义忠实度", report["citation_metric_note"])
                # 样例金标刻意只标注一半，报告必须如实区分已标注与未标注。
                self.assertEqual(report["faithfulness_labeled_cases"], 6)
                self.assertEqual(report["faithfulness_unlabeled_cases"], 6)
                self.assertEqual(report["faithfulness_stale_cases"], 0)
                self.assertEqual(report["semantic_faithfulness_rate"], 1.0)
                self.assertIn("不按 1.0 计算", report["faithfulness_metric_note"])
                self.assertGreater(report["average_latency_ms"], 0)
                self.assertTrue(
                    all(item["answer_mode"] == "offline" for item in report["details"])
                )
                saved = json.loads(output.read_text(encoding="utf-8"))
                self.assertEqual(saved["cases"], report["cases"])
                self.assertIn("details", saved)

    def test_unlabeled_faithfulness_is_na_not_one(self) -> None:
        with KnowledgeStore(":memory:") as store:
            store.ingest_document("known.txt", "公司差旅制度。住宿限额为每晚五百元。")
            report = evaluate_cases(
                [GoldCase("住宿限额是多少", ("known.txt",))], HybridRetriever(store)
            )
        self.assertIsNone(report["semantic_faithfulness_rate"])
        self.assertEqual(report["faithfulness_labeled_cases"], 0)
        self.assertEqual(report["faithfulness_unlabeled_cases"], 1)
        self.assertEqual(report["faithfulness_stale_cases"], 0)
        self.assertEqual(report["details"][0]["faithfulness_status"], "unlabeled")
        self.assertIsNone(report["details"][0]["faithfulness"])

    def test_partial_faithfulness_annotations_only_aggregate_labeled(self) -> None:
        with KnowledgeStore(":memory:") as store:
            store.ingest_document(
                "known.txt", "公司差旅制度。住宿限额为每晚五百元。"
            )
            retriever = HybridRetriever(store)
            supported = answer_question(
                "住宿限额是多少", retriever.search("住宿限额是多少", top_k=3)
            )
            # 第二条标注用于验证聚合口径，不代表真实质量判断。
            partial = answer_question(
                "报销时限是多久", retriever.search("报销时限是多久", top_k=3)
            )
            cases = [
                GoldCase(
                    "住宿限额是多少",
                    ("known.txt",),
                    FaithfulnessAnnotation(
                        True, answer_sha256(str(supported["answer"]))
                    ),
                ),
                GoldCase(
                    "报销时限是多久",
                    ("known.txt",),
                    FaithfulnessAnnotation(
                        False, answer_sha256(str(partial["answer"]))
                    ),
                ),
                GoldCase("远程办公补贴怎么发", ("known.txt",)),
            ]
            report = evaluate_cases(cases, retriever)
        self.assertEqual(report["faithfulness_labeled_cases"], 2)
        self.assertEqual(report["faithfulness_unlabeled_cases"], 1)
        self.assertEqual(report["faithfulness_stale_cases"], 0)
        self.assertEqual(report["semantic_faithfulness_rate"], 0.5)
        self.assertEqual(report["details"][0]["faithfulness"], True)
        self.assertEqual(report["details"][1]["faithfulness"], False)

    def test_stale_faithfulness_fingerprint_is_na(self) -> None:
        stale = FaithfulnessAnnotation(True, "0" * 64)
        with KnowledgeStore(":memory:") as store:
            store.ingest_document("known.txt", "公司差旅制度。住宿限额为每晚五百元。")
            report = evaluate_cases(
                [GoldCase("住宿限额是多少", ("known.txt",), stale)],
                HybridRetriever(store),
            )
        # 回答变化后旧标注必须失效，而不是继续按 1.0 计入。
        self.assertIsNone(report["semantic_faithfulness_rate"])
        self.assertEqual(report["faithfulness_labeled_cases"], 0)
        self.assertEqual(report["faithfulness_stale_cases"], 1)
        self.assertEqual(report["details"][0]["faithfulness_status"], "stale")
        self.assertIsNone(report["details"][0]["faithfulness"])

    def test_invalid_faithfulness_annotations_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            GoldCase.from_dict(
                {"question": "问", "expected_sources": ["a.txt"], "faithfulness": []}
            )
        with self.assertRaises(ValueError):
            GoldCase.from_dict(
                {
                    "question": "问",
                    "expected_sources": ["a.txt"],
                    "faithfulness": {"faithful": "true"},
                }
            )
        with self.assertRaises(ValueError):
            GoldCase.from_dict(
                {
                    "question": "问",
                    "expected_sources": ["a.txt"],
                    "faithfulness": {"faithful": True, "answer_sha256": "abc"},
                }
            )
        parsed = FaithfulnessAnnotation.from_dict(
            {"faithful": True, "answer_sha256": "A" * 64}
        )
        self.assertEqual(parsed.answer_sha256, "a" * 64)
        self.assertEqual(
            GoldCase("问", ("a.txt",), parsed).to_dict()["faithfulness"],
            {"faithful": True, "answer_sha256": "a" * 64},
        )

    def test_zero_citations_is_na_not_a_perfect_score(self) -> None:
        with KnowledgeStore(":memory:") as store:
            store.ingest_document("known.txt", "公司差旅制度。住宿限额为每晚五百元。")
            report = evaluate_cases(
                [GoldCase("火星殖民船票怎么买", ("known.txt",))],
                HybridRetriever(store),
            )
            self.assertIsNone(report["citation_id_valid_rate"])
            self.assertEqual(report["details"][0]["total_citation_ids"], 0)

    def test_multi_intent_answer_extracts_direct_facts(self) -> None:
        with KnowledgeStore(":memory:") as store:
            store.ingest_directory(ROOT / "data" / "sample_docs")
            retriever = HybridRetriever(store)
            question = "差旅住宿标准和报销时限是什么？"
            answer = answer_question(question, retriever.search(question, top_k=3))
            self.assertIn("五百五十元", str(answer["answer"]))
            self.assertIn("十个自然日", str(answer["answer"]))

            security_question = "发现安全事件后应该多久报告？"
            security_answer = answer_question(
                security_question, retriever.search(security_question, top_k=3)
            )
            self.assertIn("三十分钟", str(security_answer["answer"]))
            self.assertNotIn("五、安全事件报告与处置 [", str(security_answer["answer"]))


if __name__ == "__main__":
    unittest.main()
