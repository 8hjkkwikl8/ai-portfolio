from __future__ import annotations

import csv
import math
import tempfile
import unittest
from pathlib import Path

from app.chart import bar_chart, line_chart
from app.database import DEFAULT_CSV_PATH
from app.report import generate_html_report
from examples.generate_data import generate_rows


class ChartTests(unittest.TestCase):
    def test_bar_chart_escapes_labels_and_title(self) -> None:
        svg = bar_chart(["<华东&\"'>"], [1280], '<销售额&"分析">')
        self.assertTrue(svg.startswith("<svg"))
        self.assertNotIn("<华东", svg)
        self.assertNotIn("<销售额", svg)
        self.assertIn("&lt;华东&amp;&quot;&#x27;&gt;", svg)
        self.assertIn("&lt;销售额&amp;&quot;分析&quot;&gt;", svg)
        self.assertNotIn("<script", svg.casefold())

    def test_line_chart_escapes_script_like_label(self) -> None:
        svg = line_chart(["<script>alert(1)</script>", "2025-02"], [1, 2], "趋势")
        self.assertNotIn("<script>", svg)
        self.assertIn("&lt;script&gt;", svg)
        self.assertIn("<polyline", svg)

    def test_chart_rejects_non_finite_numbers(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value), self.assertRaises(ValueError):
                bar_chart(["x"], [value])

    def test_empty_chart_is_safe(self) -> None:
        self.assertIn("暂无可绘制数据", bar_chart([], [], "空图"))


class ReportTests(unittest.TestCase):
    def test_single_file_report_escapes_text_and_embeds_svg(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "report.html"
            generate_html_report(
                [
                    {
                        "question": "<img src=x onerror=alert(1)>销售额",
                        "sql": "SELECT '<tag>' AS value",
                        "summary": "<b>摘要</b>",
                        "columns": ["name", "value"],
                        "rows": [["<script>alert(1)</script>", 10]],
                        "chart": bar_chart(["<标签>"], [10]),
                        "mode": "离线规则",
                    }
                ],
                destination,
            )
            document = destination.read_text(encoding="utf-8")
            self.assertNotIn("<img src=x", document)
            self.assertNotIn("<script>alert(1)</script>", document)
            self.assertNotIn("<b>摘要</b>", document)
            self.assertIn("&lt;img src=x", document)
            self.assertIn("&lt;script&gt;", document)
            self.assertIn("<svg", document)
            self.assertNotIn("localStorage", document)
            self.assertNotIn("https://cdn", document)


class DemoDataTests(unittest.TestCase):
    def test_deterministic_rows_cover_month_category_and_region(self) -> None:
        first = generate_rows()
        second = generate_rows()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 48)
        self.assertEqual(len({row["order_id"] for row in first}), 48)
        self.assertEqual(len({str(row["order_date"])[:7] for row in first}), 6)
        self.assertEqual(len({row["category"] for row in first}), 4)
        self.assertEqual(len({row["region"] for row in first}), 4)
        self.assertEqual(len({row["channel"] for row in first}), 4)

    def test_csv_matches_generated_data(self) -> None:
        with DEFAULT_CSV_PATH.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(rows), 48)
        self.assertEqual(rows[0]["order_id"], generate_rows()[0]["order_id"])


if __name__ == "__main__":
    unittest.main()
