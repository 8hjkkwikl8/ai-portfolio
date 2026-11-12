from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.audit import AUDIT_FIELDS, QueryAuditLogger, sql_sha256
from app.database import QueryExecutionError, QueryTimeoutError, ReadOnlyDatabase, create_demo_database
from app.planner import PlannerError, QueryPlan

try:
    from app.api import QueryService
except ImportError:  # 未安装 FastAPI 时，其余核心测试仍可独立运行。
    QueryService = None  # type: ignore[assignment,misc]


@unittest.skipIf(QueryService is None, "未安装 FastAPI/httpx，跳过查询服务审计测试")
class QueryAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        root = Path(self.temporary_directory.name)
        self.database = ReadOnlyDatabase(create_demo_database(root / "audit.db"))
        self.audit_path = root / "logs" / "query_audit.jsonl"
        self.logger = QueryAuditLogger(self.audit_path)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def events(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]

    def test_success_records_hashes_rows_and_redacted_question(self) -> None:
        secret_param = "智能家居"
        secret_key = "sk-test-1234567890"

        class ParameterizedPlanner:
            mode = "测试参数规划器"

            def plan(self, question: str) -> QueryPlan:
                return QueryPlan(
                    question,
                    "SELECT category, SUM(quantity) AS total_quantity FROM orders "
                    "WHERE category = ? GROUP BY category",
                    (secret_param,),
                    "测试参数化查询。",
                    mode=self.mode,
                )

        secrets = (
            secret_param,
            secret_key,
            "abc123def456",
            "plain-token-secret",
            "chinese-secret",
            "bearer-secret-123",
        )
        question = (
            f"OPENAI_API_KEY=abc123def456 API_KEY={secret_key} token=plain-token-secret "
            "密码：chinese-secret Authorization: Bearer bearer-secret-123"
        )
        service = QueryService(self.database, ParameterizedPlanner(), self.logger)
        result = service.ask(question)
        event = self.events()[0]

        self.assertEqual(set(event), set(AUDIT_FIELDS))
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["guard_result"], "passed")
        self.assertEqual(event["row_count"], 1)
        self.assertEqual(event["guarded_sql_sha256"], sql_sha256(result["sql"]))
        self.assertEqual(len(str(event["sql_sha256"])), 64)
        self.assertIsInstance(event["duration_ms"], float)
        self.assertGreaterEqual(event["duration_ms"], 0)
        self.assertTrue(str(event["timestamp"]).endswith("Z"))
        self.assertIn("API_KEY=<redacted>", str(event["question"]))
        self.assertIn("OPENAI_API_KEY=<redacted>", str(event["question"]))
        self.assertIn("token=<redacted>", str(event["question"]))
        self.assertIn("密码=<redacted>", str(event["question"]))
        self.assertIn("Authorization=<redacted>", str(event["question"]))

        serialized = self.audit_path.read_text(encoding="utf-8")
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, serialized)
        self.assertNotIn("SELECT category", serialized)
        self.assertNotIn("params", event)

    def test_planning_and_guard_failures_are_audited_without_raw_details(self) -> None:
        class RejectingPlanner:
            mode = "测试拒绝规划器"

            def plan(self, question: str) -> QueryPlan:
                raise PlannerError("规划错误原文不应进入审计日志")

        service = QueryService(self.database, RejectingPlanner(), self.logger)
        with self.assertRaises(PlannerError):
            service.ask("无法规划的问题")

        class MaliciousPlanner:
            mode = "测试恶意规划器"

            def plan(self, question: str) -> QueryPlan:
                return QueryPlan(
                    question,
                    "DROP TABLE orders WHERE category = 'raw-secret-param'",
                    (),
                    "恶意候选。",
                    mode=self.mode,
                )

        service = QueryService(self.database, MaliciousPlanner(), self.logger)
        with self.assertRaises(ValueError):
            service.ask("守护拒绝测试")
        planning_event, guard_event = self.events()

        self.assertEqual(planning_event["status"], "planning_rejected")
        self.assertEqual(planning_event["guard_result"], "not_run")
        self.assertIsNone(planning_event["sql_sha256"])
        self.assertEqual(planning_event["error_type"], "PlannerError")
        self.assertEqual(guard_event["status"], "guard_rejected")
        self.assertEqual(guard_event["guard_result"], "rejected")
        self.assertEqual(guard_event["error_type"], "SQLGuardError")
        self.assertEqual(len(str(guard_event["sql_sha256"])), 64)
        self.assertIsNone(guard_event["guarded_sql_sha256"])

        serialized = self.audit_path.read_text(encoding="utf-8")
        self.assertNotIn("规划错误原文", serialized)
        self.assertNotIn("DROP TABLE", serialized)
        self.assertNotIn("raw-secret-param", serialized)

    def test_execution_timeout_and_internal_errors_are_safely_audited(self) -> None:
        class ValidPlanner:
            mode = "测试执行异常"

            def plan(self, question: str) -> QueryPlan:
                return QueryPlan(question, "SELECT COUNT(*) AS order_count FROM orders", (), "测试。", mode=self.mode)

        service = QueryService(self.database, ValidPlanner(), self.logger)
        cases = (
            ("execution_error", QueryExecutionError("secret-execution-detail")),
            ("timeout", QueryTimeoutError("secret-timeout-detail")),
            ("internal_error", RuntimeError("secret-internal-detail")),
        )
        for expected_status, error in cases:
            with self.subTest(status=expected_status):
                with patch.object(self.database, "execute_guarded", side_effect=error):
                    with self.assertRaises(type(error)):
                        service.ask(f"{expected_status} 测试")
                event = self.events()[-1]
                self.assertEqual(event["status"], expected_status)
                self.assertEqual(event["guard_result"], "passed")
                self.assertEqual(event["error_type"], type(error).__name__)
                self.assertIsNone(event["row_count"])
                self.assertIsNotNone(event["guarded_sql_sha256"])

        serialized = self.audit_path.read_text(encoding="utf-8")
        for _, error in cases:
            self.assertNotIn(str(error), serialized)


if __name__ == "__main__":
    unittest.main()
