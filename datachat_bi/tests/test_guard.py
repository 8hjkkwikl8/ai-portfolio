from __future__ import annotations

import unittest

from app.guard import SQLGuard, SQLGuardError


class SQLGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.guard = SQLGuard(max_rows=25)

    def assert_rejected(self, sql: str) -> None:
        with self.subTest(sql=sql), self.assertRaises(SQLGuardError):
            self.guard.validate(sql)

    def test_valid_select_gets_automatic_limit(self) -> None:
        guarded = self.guard.validate("SELECT order_id, revenue FROM orders ORDER BY order_id")
        self.assertTrue(guarded.sql.endswith("LIMIT 25"))
        self.assertEqual(guarded.params, ())
        self.assertEqual(guarded.max_rows, 25)

    def test_trailing_semicolon_is_single_statement(self) -> None:
        guarded = self.guard.validate("SELECT category FROM orders;")
        self.assertNotIn(";", guarded.sql)
        self.assertTrue(guarded.sql.endswith("LIMIT 25"))

    def test_existing_limit_is_preserved_or_clamped(self) -> None:
        self.assertTrue(self.guard.validate("SELECT * FROM orders LIMIT 7").sql.endswith("LIMIT 7"))
        self.assertTrue(self.guard.validate("SELECT * FROM orders LIMIT 900").sql.endswith("LIMIT 25"))
        self.assertTrue(
            self.guard.validate("SELECT * FROM orders LIMIT 10 OFFSET 2").sql.endswith(
                "LIMIT 10 OFFSET 2"
            )
        )

    def test_drop_attach_and_other_write_operations(self) -> None:
        statements = (
            "DROP TABLE orders",
            "ATTACH DATABASE 'other.db' AS other",
            "PRAGMA table_info(orders)",
            "UPDATE orders SET revenue = 0",
            "INSERT INTO orders(order_id) VALUES ('x')",
            "DELETE FROM orders",
            "CREATE TABLE x(value TEXT)",
            "ALTER TABLE orders ADD COLUMN secret TEXT",
            "VACUUM",
        )
        for statement in statements:
            self.assert_rejected(statement)

    def test_multiple_statements_are_rejected(self) -> None:
        for statement in (
            "SELECT * FROM orders; DROP TABLE orders",
            "SELECT 1; SELECT 2",
            "SELECT * FROM orders;;",
            "WITH x AS (SELECT * FROM orders) SELECT * FROM x; DELETE FROM orders",
        ):
            self.assert_rejected(statement)

    def test_comment_bypass_is_rejected(self) -> None:
        for statement in (
            "SELECT * FROM orders -- hide trailing text",
            "SELECT * FROM orders /* comment */ LIMIT 2",
            "SELECT * FROM orders; -- comment\nDROP TABLE orders",
            "SELECT * FROM orders/**/WHERE revenue > 0",
        ):
            self.assert_rejected(statement)

    def test_comment_markers_inside_string_are_not_comments(self) -> None:
        guarded = self.guard.validate("SELECT '--not a comment' AS note")
        self.assertIn("--not a comment", guarded.sql)

    def test_unauthorized_tables_and_schema_qualifiers(self) -> None:
        for statement in (
            "SELECT * FROM sqlite_master",
            "SELECT * FROM secrets",
            "SELECT * FROM main.orders",
            "SELECT * FROM orders, secrets",
            "SELECT * FROM (SELECT * FROM orders) AS safe, secrets",
            "WITH x AS (SELECT * FROM sqlite_master) SELECT * FROM x",
        ):
            self.assert_rejected(statement)

    def test_unauthorized_or_unknown_qualified_columns(self) -> None:
        self.assert_rejected("SELECT orders.password FROM orders")
        self.assert_rejected("SELECT o.password FROM orders AS o")

    def test_join_on_whitelisted_tables_is_allowed(self) -> None:
        guarded = self.guard.validate(
            "SELECT p.product_line, ROUND(SUM(o.revenue), 2) AS total_revenue "
            "FROM orders o JOIN products p ON o.category = p.category "
            "GROUP BY p.product_line"
        )
        self.assertIn("JOIN products p", guarded.sql)
        self.assertTrue(guarded.sql.endswith("LIMIT 25"))

    def test_join_rejects_unlisted_tables_and_columns(self) -> None:
        for statement in (
            "SELECT * FROM orders o JOIN secrets s ON o.category = s.category",
            "SELECT * FROM products p JOIN sqlite_master m ON 1 = 1",
            "SELECT p.password FROM products p",
            "SELECT c.unknown_column FROM channels c",
            "SELECT * FROM main.products",
            "SELECT * FROM products, secrets",
        ):
            self.assert_rejected(statement)

    def test_non_select_is_rejected(self) -> None:
        for statement in ("VALUES(1)", "EXPLAIN SELECT * FROM orders", "BEGIN TRANSACTION"):
            self.assert_rejected(statement)

    def test_with_cannot_hide_writes(self) -> None:
        for statement in (
            "WITH x AS (DELETE FROM orders) SELECT * FROM x",
            "WITH x AS (UPDATE orders SET revenue = 0) SELECT * FROM x",
            "WITH x AS (INSERT INTO orders(order_id) VALUES('x')) SELECT * FROM x",
            "WITH x AS (SELECT * FROM orders) DELETE FROM orders",
            "WITH x AS (SELECT * FROM orders) DROP TABLE orders",
        ):
            self.assert_rejected(statement)

    def test_valid_with_query(self) -> None:
        guarded = self.guard.validate(
            "WITH category_sales AS ("
            "SELECT category, SUM(revenue) AS total_revenue FROM orders GROUP BY category"
            ") SELECT category, total_revenue FROM category_sales ORDER BY total_revenue DESC"
        )
        self.assertIn("WITH category_sales", guarded.sql)
        self.assertTrue(guarded.sql.endswith("LIMIT 25"))

    def test_recursive_cte_and_cte_column_list(self) -> None:
        guarded = self.guard.validate(
            "WITH RECURSIVE cnt(x) AS ("
            "SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 5"
            ") SELECT x FROM cnt"
        )
        self.assertIn("RECURSIVE", guarded.sql)

    def test_dangerous_and_unknown_functions(self) -> None:
        for statement in (
            "SELECT load_extension('x')",
            "SELECT readfile('/etc/passwd')",
            "SELECT writefile('x', 'y')",
            "SELECT unknown_extension_function(order_id) FROM orders",
        ):
            self.assert_rejected(statement)

    def test_in_subquery_case_and_parameters_are_valid(self) -> None:
        guarded = self.guard.validate(
            "SELECT category, CASE WHEN revenue > ? THEN 'high' ELSE 'low' END AS tier "
            "FROM orders WHERE category IN (?, ?) AND EXISTS "
            "(SELECT 1 FROM orders AS o WHERE o.category = orders.category)",
            (100, "厨房电器", "智能家居"),
        )
        self.assertEqual(guarded.params, (100, "厨房电器", "智能家居"))
        self.assertIn("IN (?, ?)", guarded.sql)

    def test_limit_parameter_and_negative_limit_are_rejected(self) -> None:
        self.assert_rejected("SELECT * FROM orders LIMIT ?")
        self.assert_rejected("SELECT * FROM orders LIMIT -1")
        self.assert_rejected("SELECT * FROM orders LIMIT 1, 2")

    def test_forbidden_keyword_inside_string_is_safe(self) -> None:
        guarded = self.guard.validate("SELECT 'DROP TABLE orders' AS example")
        self.assertIn("DROP TABLE orders", guarded.sql)

    def test_parameter_container_must_be_positional(self) -> None:
        with self.assertRaises(SQLGuardError):
            self.guard.validate("SELECT * FROM orders WHERE category = ?", {"category": "厨房电器"})


if __name__ == "__main__":
    unittest.main()
