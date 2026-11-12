"""FastAPI HTTP 接口与查询编排。"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .audit import QueryAuditLogger, new_query_event, resolve_audit_log_path, sql_sha256
from .chart import build_chart, infer_chart
from .database import (
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
    """规则与 LLM 共用的规划、守护、只读执行与脱敏审计链路。"""

    def __init__(
        self,
        database: ReadOnlyDatabase,
        planner: Planner,
        audit_logger: QueryAuditLogger | None = None,
    ) -> None:
        self.database = database
        self.planner = planner
        self.audit_logger = audit_logger

    def ask(self, question: str) -> dict[str, Any]:
        started = time.monotonic()
        event = new_query_event(question, self.planner.mode)
        try:
            plan = self.planner.plan(question)
            event["sql_sha256"] = sql_sha256(plan.sql)
            guarded = self.database.guard.validate(plan.sql, plan.params)
            event["guard_result"] = "passed"
            event["guarded_sql_sha256"] = sql_sha256(guarded.sql)
            result = self.database.execute_guarded(guarded)
        except PlannerError as exc:
            event.update(status="planning_rejected", error_type=type(exc).__name__)
            raise
        except SQLGuardError as exc:
            event.update(status="guard_rejected", guard_result="rejected", error_type=type(exc).__name__)
            raise
        except QueryTimeoutError as exc:
            event.update(status="timeout", error_type=type(exc).__name__)
            raise
        except QueryExecutionError as exc:
            event.update(status="execution_error", error_type=type(exc).__name__)
            raise
        except Exception as exc:
            event.update(status="internal_error", error_type=type(exc).__name__)
            raise
        else:
            event.update(status="success", row_count=result.row_count)
        finally:
            event["duration_ms"] = round((time.monotonic() - started) * 1000, 3)
            if self.audit_logger is not None:
                self.audit_logger.record(event)

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
    audit_log_path: str | os.PathLike[str] | None = None,
) -> FastAPI:
    database_path = resolve_database_path(db_path)
    if not database_path.exists():
        create_demo_database(database_path, overwrite=False)
    database = ReadOnlyDatabase(database_path, max_rows=max_rows, timeout_seconds=timeout_seconds)
    selected_planner = planner or planner_from_environment()
    resolved_audit_path = resolve_audit_log_path(audit_log_path)
    audit_logger = QueryAuditLogger(resolved_audit_path) if resolved_audit_path is not None else None
    service = QueryService(database, selected_planner, audit_logger)

    application = FastAPI(
        title="DataChat BI",
        description="自然语言到安全 SQL 的只读 SQLite 分析演示",
        version="1.0.0",
    )
    application.state.database = database
    application.state.planner = selected_planner
    application.state.query_service = service
    application.state.audit_log_enabled = audit_logger is not None

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
            "audit_log": "enabled" if audit_logger is not None else "disabled",
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
    audit_group = parser.add_mutually_exclusive_group()
    audit_group.add_argument(
        "--audit-log",
        default=os.getenv("DATACHAT_AUDIT_LOG"),
        help="JSONL 审计日志路径；默认 datachat_bi/artifacts/query_audit.jsonl",
    )
    audit_group.add_argument("--no-audit-log", action="store_true", help="关闭本地查询审计日志")
    args = parser.parse_args()

    import uvicorn

    selected_audit_path = "off" if args.no_audit_log else args.audit_log
    uvicorn.run(create_app(args.db, audit_log_path=selected_audit_path), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
