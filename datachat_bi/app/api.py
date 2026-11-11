"""FastAPI HTTP 接口与查询编排。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .chart import build_chart, infer_chart
from .database import (
    PROJECT_ROOT,
    QueryExecutionError,
    QueryTimeoutError,
    ReadOnlyDatabase,
    create_demo_database,
    resolve_database_path,
)
from .guard import TABLE_SCHEMA, SQLGuardError
from .planner import Planner, PlannerError, planner_from_environment

STATIC_INDEX = Path(__file__).resolve().parent / "static" / "index.html"


class QueryRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


class QueryService:
    """规则与 LLM 共用的单次规划、统一守护、只读执行链路。"""

    def __init__(self, database: ReadOnlyDatabase, planner: Planner) -> None:
        self.database = database
        self.planner = planner

    def ask(self, question: str) -> dict[str, Any]:
        plan = self.planner.plan(question)
        result = self.database.execute(plan.sql, plan.params)
        chart = build_chart(plan.chart, result.columns, result.rows) if plan.chart else infer_chart(
            result.columns, result.rows
        )
        return {
            "question": question,
            "sql": result.sql,
            "summary": f"{plan.summary} 返回 {result.row_count} 行，结果上限 {self.database.guard.max_rows} 行。",
            "columns": result.columns,
            "rows": result.rows,
            "row_count": result.row_count,
            "chart": chart,
            "mode": plan.mode,
        }


def create_app(
    db_path: str | os.PathLike[str] | None = None,
    *,
    planner: Planner | None = None,
    timeout_seconds: float = 2.0,
    max_rows: int = 200,
) -> FastAPI:
    database_path = resolve_database_path(db_path)
    if not database_path.exists():
        create_demo_database(database_path, overwrite=False)
    database = ReadOnlyDatabase(database_path, max_rows=max_rows, timeout_seconds=timeout_seconds)
    selected_planner = planner or planner_from_environment()
    service = QueryService(database, selected_planner)

    application = FastAPI(
        title="DataChat BI",
        description="自然语言到安全 SQL 的只读 SQLite 分析演示",
        version="1.0.0",
    )
    application.state.database = database
    application.state.planner = selected_planner
    application.state.query_service = service

    @application.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_INDEX, media_type="text/html; charset=utf-8")

    @application.get("/api/health")
    def health() -> dict[str, Any]:
        try:
            result = database.execute("SELECT COUNT(*) AS order_count FROM orders")
        except QueryExecutionError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from None
        return {
            "status": "ok",
            "database": "ready",
            "order_count": result.rows[0][0],
            "mode": selected_planner.mode,
            "llm_configured": not selected_planner.mode.startswith("离线规则"),
        }

    @application.get("/api/schema")
    def schema() -> dict[str, Any]:
        return {
            "tables": [
                {
                    "name": table,
                    "columns": [{"name": name, "type": kind} for name, kind in columns.items()],
                }
                for table, columns in TABLE_SCHEMA.items()
            ],
            "max_rows": database.guard.max_rows,
            "timeout_seconds": database.timeout_seconds,
            "mode": selected_planner.mode,
        }

    @application.post("/api/query")
    def query(payload: QueryRequest) -> dict[str, Any]:
        question = payload.question.strip()
        if not question:
            raise HTTPException(status_code=422, detail="问题不能只包含空白字符。")
        try:
            return service.ask(question)
        except (PlannerError, SQLGuardError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except QueryTimeoutError as exc:
            raise HTTPException(status_code=408, detail=str(exc)) from None
        except QueryExecutionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None

    return application


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 DataChat BI 中文 Web UI")
    parser.add_argument("--db", default=os.getenv("DATACHAT_DB"), help="SQLite 数据库路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(create_app(args.db), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
