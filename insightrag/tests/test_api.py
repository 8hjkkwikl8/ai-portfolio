from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

try:
    import fastapi  # noqa: F401
    import httpx  # noqa: F401
    from fastapi.testclient import TestClient
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "FastAPI/httpx 未安装，跳过 API 集成测试")
class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.api import create_app

        self.temp = tempfile.TemporaryDirectory(prefix="insightrag-api-")
        self.db_path = Path(self.temp.name) / "api.db"
        self.client = TestClient(create_app(self.db_path, load_samples=False))
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)
        self.temp.cleanup()

    def test_health_and_chinese_ui(self) -> None:
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertTrue(response.json()["offline_ready"])
        self.assertEqual(response.json()["documents"], 0)
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("让企业知识，回答有据", page.text)
        self.assertNotIn("cdn", page.text.lower())

    def test_ingest_query_evaluate_and_replacement(self) -> None:
        content = (
            "差旅管理规范。国内出差住宿限额为每晚五百元。"
            "员工应在返程后十个自然日内提交报销，并附上合法发票。"
        )
        ingested = self.client.post(
            "/api/ingest", json={"source": "unit_policy.txt", "content": content}
        )
        self.assertEqual(ingested.status_code, 200, ingested.text)
        self.assertFalse(ingested.json()["replaced"])
        self.assertEqual(ingested.json()["documents"], 1)

        queried = self.client.post(
            "/api/query", json={"question": "出差住宿限额是多少？", "top_k": 3}
        )
        self.assertEqual(queried.status_code, 200, queried.text)
        data = queried.json()
        self.assertEqual(data["mode"], "offline")
        self.assertTrue(data["sources"])
        self.assertEqual(data["sources"][0]["source"], "unit_policy.txt")
        self.assertIn("[1]", data["answer"])
        self.assertGreaterEqual(data["latency_ms"], 0)

        evaluated = self.client.post(
            "/api/evaluate",
            json={
                "top_k": 3,
                "cases": [
                    {
                        "question": "返程后多久内提交差旅报销？",
                        "expected_sources": ["unit_policy.txt"],
                    }
                ],
            },
        )
        self.assertEqual(evaluated.status_code, 200, evaluated.text)
        self.assertEqual(evaluated.json()["recall_at_k"], 1.0)
        self.assertEqual(evaluated.json()["citation_id_valid_rate"], 1.0)

        replaced = self.client.post(
            "/api/ingest", json={"source": "unit_policy.txt", "content": content}
        )
        self.assertTrue(replaced.json()["replaced"])
        self.assertTrue(replaced.json()["unchanged"])
        self.assertEqual(self.client.get("/api/stats").json()["documents"], 1)

    def test_evaluate_accepts_faithfulness_annotations(self) -> None:
        content = "差旅管理规范。国内出差住宿限额为每晚五百元。"
        self.client.post(
            "/api/ingest", json={"source": "faith.txt", "content": content}
        )
        answer = self.client.post(
            "/api/query", json={"question": "住宿限额是多少？", "top_k": 3}
        ).json()["answer"]
        digest = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        response = self.client.post(
            "/api/evaluate",
            json={
                "top_k": 3,
                "cases": [
                    {
                        "question": "住宿限额是多少？",
                        "expected_sources": ["faith.txt"],
                        "faithfulness": {"faithful": True, "answer_sha256": digest},
                    },
                    {
                        "question": "住宿限额是多少？",
                        "expected_sources": ["faith.txt"],
                    },
                ],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["faithfulness_labeled_cases"], 1)
        self.assertEqual(data["faithfulness_unlabeled_cases"], 1)
        self.assertEqual(data["faithfulness_stale_cases"], 0)
        self.assertEqual(data["semantic_faithfulness_rate"], 1.0)
        self.assertEqual(data["details"][0]["faithfulness_status"], "labeled")
        self.assertEqual(data["details"][1]["faithfulness_status"], "unlabeled")

        invalid = self.client.post(
            "/api/evaluate",
            json={
                "cases": [
                    {
                        "question": "住宿限额是多少？",
                        "expected_sources": ["faith.txt"],
                        "faithfulness": {"faithful": "yes"},
                    }
                ]
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_ingest_does_not_accept_server_file_path(self) -> None:
        response = self.client.post(
            "/api/ingest",
            json={"source": "forbidden.txt", "path": "C:/Windows/win.ini"},
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/stats").json()["documents"], 0)


if __name__ == "__main__":
    unittest.main()
