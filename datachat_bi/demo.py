"""一键重建演示库、运行七类问题并生成单文件 HTML 报告；无需 API key。"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from app.chart import build_chart, infer_chart
from app.database import DEFAULT_CSV_PATH, ReadOnlyDatabase, create_demo_database
from app.planner import RulePlanner
from app.report import generate_html_report

PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_QUESTIONS = (
    "总销售额是多少？",
    "按类别销售额排行",
    "按月销售趋势",
    "按地区销售额",
    "销量最高的商品类别是什么？",
    "哪个渠道销量最高？",
    "各渠道销售额对比",
)


def run_demo(database_path: Path, report_path: Path) -> Path:
    create_demo_database(database_path, csv_path=DEFAULT_CSV_PATH, overwrite=True)
    database = ReadOnlyDatabase(database_path, max_rows=200, timeout_seconds=2.0)
    planner = RulePlanner()
    report_results: list[dict[str, object]] = []

    print(f"运行模式：{planner.mode}（不读取 OPENAI 环境变量，不发起网络请求）")
    print(f"数据库：{database_path.resolve()}")
    for index, question in enumerate(DEMO_QUESTIONS, start=1):
        plan = planner.plan(question)
        result = database.execute(plan.sql, plan.params)
        chart = build_chart(plan.chart, result.columns, result.rows) if plan.chart else infer_chart(
            result.columns, result.rows
        )
        summary = f"{plan.summary} 返回 {result.row_count} 行。"
        report_results.append(
            {
                "question": question,
                "sql": result.sql,
                "summary": summary,
                "columns": result.columns,
                "rows": result.rows,
                "chart": chart,
                "mode": planner.mode,
            }
        )
        print(f"\n[{index}/{len(DEMO_QUESTIONS)}] {question}")
        print(f"SQL: {result.sql}")
        if plan.params:
            print(f"绑定参数: {plan.params!r}")
        print(f"结果: {dict(zip(result.columns, result.rows[0])) if len(result.rows) == 1 else f'{result.row_count} 行'}")

    return generate_html_report(report_results, report_path, mode=planner.mode)


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="运行 DataChat BI 离线演示")
    parser.add_argument("--db", type=Path, default=PROJECT_ROOT / "examples" / "datachat_demo.db")
    parser.add_argument("--output", type=Path, default=PROJECT_ROOT / "datachat_report.html")
    args = parser.parse_args()
    report = run_demo(args.db.expanduser().resolve(), args.output.expanduser().resolve())
    print(f"\nHTML 报告已生成：{report}")


if __name__ == "__main__":
    main()
