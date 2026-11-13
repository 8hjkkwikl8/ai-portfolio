from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import (
    QueryExecutionError,
    QueryTimeoutError,
    ReadOnlyDatabase,
    create_demo_database,
)
from app.guard import SQLGuardError


class ReadOnlyDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temporary_directory.name) / "isolated.db"
        create_demo_database(self.db_path)
        self.database = ReadOnlyDatabase(self.db_path, max_rows=10, timeout_seconds=1.0)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_valid_aggregate_query_returns_columns_and_rows(self) -> None:
        result = self.database.execute(
            "SELECT category, ROUND(SUM(revenue), 2) AS total_revenue "
            "FROM orders GROUP BY category ORDER BY total_revenue DESC"
        )
        self.assertEqual(result.columns, ["category", "total_revenue"])
        self.assertEqual(len(result.rows), 4)
        self.assertTrue(result.sql.endswith("LIMIT 10"))
        self.assertIsInstance(result.rows[0][1], (int, float))

    def test_parameterized_query(self) -> None:
        result = self.database.execute(
            "SELECT category, order_id FROM orders WHERE category = ? ORDER BY order_id",
            ("厨房电器",),
        )
        self.assertGreater(result.row_count, 0)
        self.assertTrue(all(row[0] == "厨房电器" for row in result.rows))

    def test_automatic_limit_caps_result_rows(self) -> None:
        result = self.database.execute("SELECT * FROM orders ORDER BY order_id")
        self.assertEqual(result.row_count, 10)
        self.assertTrue(result.sql.endswith("LIMIT 10"))

    def test_valid_with_query_executes(self) -> None:
        result = self.database.execute(
            "WITH regional AS (SELECT region, SUM(quantity) AS qty FROM orders GROUP BY region) "
            "SELECT region, qty FROM regional ORDER BY qty DESC"
        )
        self.assertEqual(result.row_count, 4)
        self.assertEqual(result.columns, ["region", "qty"])

    def test_join_across_whitelisted_tables_executes(self) -> None:
        result = self.database.execute(
            "SELECT p.product_line, ROUND(SUM(o.revenue), 2) AS total_revenue "
            "FROM orders o JOIN products p ON o.category = p.category "
            "GROUP BY p.product_line ORDER BY total_revenue DESC"
        )
        self.assertEqual(result.columns, ["product_line", "total_revenue"])
        self.assertEqual(result.row_count, 4)
        self.assertTrue(all(row[1] > 0 for row in result.rows))

    def test_demo_data_rejects_orders_with_unknown_dimension(self) -> None:
        products_path = Path(self.temporary_directory.name) / "partial_products.csv"
        products_path.write_text(
            "category,product_line,warranty_days\n厨房电器,厨房烹饪,365\n",
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            create_demo_database(
                Path(self.temporary_directory.name) / "invalid.db",
                products_path=products_path,
            )

    def test_connection_is_read_only_for_drop_and_attach(self) -> None:
        connection = self.database.connect_read_only()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("DROP TABLE orders")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("ATTACH DATABASE ? AS other", (str(self.db_path) + ".other",))
        finally:
            connection.close()
        result = self.database.execute("SELECT COUNT(*) AS count FROM orders")
        self.assertEqual(result.rows[0][0], 48)

    def test_authorizer_denies_metadata_and_dangerous_function(self) -> None:
        connection = self.database.connect_read_only()
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("SELECT name FROM sqlite_master")
            with self.assertRaises(sqlite3.DatabaseError):
                connection.execute("SELECT load_extension('unsafe')")
        finally:
            connection.close()

    def test_query_timeout_uses_progress_handler(self) -> None:
        database = ReadOnlyDatabase(self.db_path, max_rows=10, timeout_seconds=0.00001)
        with self.assertRaises(QueryTimeoutError):
            database.execute(
                "WITH RECURSIVE cnt(x) AS ("
                "SELECT 1 UNION ALL SELECT x + 1 FROM cnt WHERE x < 100000000"
                ") SELECT SUM(x) AS total FROM cnt"
            )

    def test_execution_errors_do_not_expose_path_or_sql(self) -> None:
        with self.assertRaises(QueryExecutionError) as caught:
            self.database.execute("SELECT revenue FROM orders WHERE")
        message = str(caught.exception)
        self.assertNotIn(str(self.db_path), message)
        self.assertNotIn("SELECT revenue", message)

    def test_write_is_rejected_before_execution(self) -> None:
        with self.assertRaises(SQLGuardError):
            self.database.execute("DELETE FROM orders")


if __name__ == "__main__":
    unittest.main()
