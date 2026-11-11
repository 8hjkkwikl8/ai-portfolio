from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.database import create_demo_database
from app.planner import QueryPlan, RulePlanner

try:
    from fastapi.testclient import TestClient
    from app.api import create_app
except ImportError:  # 核心 guard/database/planner 测试不依赖 FastAPI。
    TestClient = None  # type: ignore[assignment,misc]
    create_app = None  # type: ignore[assignment]


@unittest.skipIf(TestClient is None, "未安装 FastAPI/httpx，跳过 HTTP 集成测试")
class APITests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "api-isolated.db"
        create_demo_database(self.db_path)
        self.app = create_app(self.db_path, planner=RulePlanner(), max_rows=20)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.client.close()
        self.temporary_directory.cleanup()

    def test_health_and_schema(self) -> None:
        health = self.client.get("/api/health")
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "ok")
        self.assertEqual(health.json()["order_count"], 48)
        self.assertIn("离线规则", health.json()["mode"])

        schema = self.client.get("/api/schema")
        self.assertEqual(schema.status_code, 200)
        self.assertEqual(schema.json()["tables"][0]["name"], "orders")
        self.assertEqual(len(schema.json()["tables"][0]["columns"]), 7)
        self.assertEqual(schema.json()["max_rows"], 20)

    def test_root_is_chinese_ui(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("DataChat BI", response.text)
        self.assertIn("离线规则", response.text)
        self.assertNotIn("https://cdn", response.text)

    def test_valid_query_returns_sql_rows_and_svg(self) -> None:
        response = self.client.post("/api/query", json={"question": "按类别销售额排行"})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["row_count"], 4)
        self.assertEqual(data["columns"][:2], ["category", "total_revenue"])
        self.assertIn("LIMIT 20", data["sql"])
        self.assertTrue(data["chart"].startswith("<svg"))
        self.assertIn("离线规则", data["mode"])

    def test_unknown_question_returns_safe_examples(self) -> None:
        response = self.client.post("/api/query", json={"question": "预测明年公司战略"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("示例", response.json()["detail"])
        self.assertIn("按月销售趋势", response.json()["detail"])

    def test_blank_question_is_rejected(self) -> None:
        response = self.client.post("/api/query", json={"question": "   "})
        self.assertEqual(response.status_code, 422)

    def test_environment_database_path_is_honored(self) -> None:
        with patch.dict(os.environ, {"DATACHAT_DB": str(self.db_path)}):
            application = create_app(planner=RulePlanner())
        self.assertEqual(application.state.database.path, self.db_path.resolve())

    def test_malicious_llm_candidate_still_passes_guard(self) -> None:
        class MaliciousPlanner:
            mode = "测试 LLM"

            def plan(self, question: str) -> QueryPlan:
                return QueryPlan(question, question, (), "测试候选", None, self.mode)

        application = create_app(self.db_path, planner=MaliciousPlanner())
        client = TestClient(application)
        try:
            for statement in ("DROP TABLE orders", "ATTACH DATABASE 'x' AS x", "SELECT * FROM sqlite_master"):
                with self.subTest(statement=statement):
                    response = client.post("/api/query", json={"question": statement})
                    self.assertEqual(response.status_code, 400)
            health = client.get("/api/health")
            self.assertEqual(health.json()["order_count"], 48)
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
