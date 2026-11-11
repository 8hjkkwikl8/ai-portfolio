from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.guard import SQLGuard
from app.planner import (
    EXAMPLE_QUESTIONS,
    OpenAICompatiblePlanner,
    PlannerError,
    RulePlanner,
    planner_from_environment,
)


class RulePlannerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = RulePlanner()
        self.guard = SQLGuard(max_rows=50)

    def test_every_example_has_valid_guarded_sql(self) -> None:
        for question in EXAMPLE_QUESTIONS:
            with self.subTest(question=question):
                plan = self.planner.plan(question)
                guarded = self.guard.validate(plan.sql, plan.params)
                self.assertTrue(guarded.sql.upper().startswith(("SELECT", "WITH")))
                self.assertIn("orders", guarded.sql)
                self.assertEqual(plan.mode, self.planner.mode)

    def test_chart_types(self) -> None:
        self.assertEqual(self.planner.plan("按月销售趋势").chart.kind, "line")  # type: ignore[union-attr]
        self.assertEqual(self.planner.plan("按地区销售额").chart.kind, "bar")  # type: ignore[union-attr]
        self.assertIsNone(self.planner.plan("总销售额是多少？").chart)

    def test_highest_quantity_queries_are_specific(self) -> None:
        category = self.planner.plan("销量最高的商品类别是什么？")
        channel = self.planner.plan("哪个渠道销量最高？")
        self.assertIn("total_quantity", category.sql)
        self.assertIn("LIMIT 1", category.sql)
        self.assertIn("category", category.sql)
        self.assertIn("channel", channel.sql)

    def test_year_is_a_bound_parameter_not_sql_literal(self) -> None:
        for question in ("2025年按地区销售额", "2025年总销售额是多少？"):
            with self.subTest(question=question):
                plan = self.planner.plan(question)
                self.assertEqual(plan.params, ("2025",))
                self.assertIn("= ?", plan.sql)
                self.assertNotIn("2025", plan.sql)
                self.guard.validate(plan.sql, plan.params)

    def test_unconsumed_filters_and_multiple_years_are_rejected(self) -> None:
        questions = (
            "按地区销售额，仅看华东",
            "2025年按月销售趋势，只看线上渠道",
            "销量最高的商品类别是什么？请只看厨房电器",
            "2024年和2025年按地区销售额",
        )
        for question in questions:
            with self.subTest(question=question):
                with self.assertRaises(PlannerError):
                    self.planner.plan(question)

    def test_unknown_question_returns_examples_instead_of_guessing(self) -> None:
        with self.assertRaises(PlannerError) as caught:
            self.planner.plan("随便分析一下公司未来战略")
        message = str(caught.exception)
        self.assertIn("并非任意自然语言理解", message)
        self.assertIn("按月销售趋势", message)


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, size: int) -> bytes:
        return self.body[:size]


class OpenAICompatiblePlannerTests(unittest.TestCase):
    def test_structured_candidate_and_params_are_parsed_once(self) -> None:
        candidate = {"sql": "SELECT * FROM orders WHERE category = ?", "params": ["厨房电器"]}
        response = FakeHTTPResponse(
            {"choices": [{"message": {"content": json.dumps(candidate, ensure_ascii=False)}}]}
        )
        planner = OpenAICompatiblePlanner(
            base_url="https://llm.example/v1", api_key="test-key", model="test-model"
        )
        with patch("app.planner.urllib.request.urlopen", return_value=response) as mocked:
            plan = planner.plan("厨房电器订单")
        self.assertEqual(mocked.call_count, 1)
        self.assertEqual(plan.sql, candidate["sql"])
        self.assertEqual(plan.params, ("厨房电器",))
        SQLGuard().validate(plan.sql, plan.params)

    def test_invalid_llm_response_never_becomes_sql(self) -> None:
        response = FakeHTTPResponse({"choices": [{"message": {"content": "DROP TABLE orders"}}]})
        planner = OpenAICompatiblePlanner(
            base_url="https://llm.example/v1", api_key="test-key", model="test-model"
        )
        with patch("app.planner.urllib.request.urlopen", return_value=response):
            with self.assertRaises(PlannerError):
                planner.plan("删表")

    def test_missing_environment_uses_rule_planner(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "", "OPENAI_MODEL": ""}):
            self.assertIsInstance(planner_from_environment(), RulePlanner)


if __name__ == "__main__":
    unittest.main()
