"""演示数据库初始化与多层只读查询执行。"""

from __future__ import annotations

import csv
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .guard import ALLOWED_FUNCTIONS, TABLE_SCHEMA, GuardedQuery, SQLGuard

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "examples" / "datachat_demo.db"
DEFAULT_CSV_PATH = PROJECT_ROOT / "examples" / "orders.csv"
DEFAULT_PRODUCTS_PATH = PROJECT_ROOT / "examples" / "products.csv"
DEFAULT_CHANNELS_PATH = PROJECT_ROOT / "examples" / "channels.csv"
DEFAULT_SCHEMA_PATH = PROJECT_ROOT / "examples" / "schema.sql"


class QueryExecutionError(RuntimeError):
    """不包含数据库路径、SQL 原文或底层异常的安全错误。"""


class QueryTimeoutError(QueryExecutionError):
    """查询超过墙钟时间预算。"""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[object]]
    sql: str

    @property
    def row_count(self) -> int:
        return len(self.rows)


def _load_dimension_rows(
    path: str | os.PathLike[str], table: str
) -> tuple[list[tuple[object, ...]], set[str]]:
    """读取维度 CSV，按 schema 声明转换类型并校验主键唯一。"""

    source_path = Path(path).expanduser().resolve()
    required = list(TABLE_SCHEMA[table])
    with source_path.open("r", encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    if not source_rows:
        raise ValueError(f"{source_path.name} 至少需要一行维度数据")
    if list(source_rows[0]) != required:
        raise ValueError(f"{source_path.name} 表头与内置 schema 不一致")

    rows: list[tuple[object, ...]] = []
    keys: set[str] = set()
    for line_number, row in enumerate(source_rows, start=2):
        key = row[required[0]].strip()
        if not key or key in keys:
            raise ValueError(f"{source_path.name} 第 {line_number} 行主键为空或重复")
        keys.add(key)
        values: list[object] = [key]
        for column in required[1:]:
            raw = row[column].strip()
            kind = TABLE_SCHEMA[table][column]
            try:
                if kind == "INTEGER":
                    value: object = int(raw)
                elif kind == "REAL":
                    value = float(raw)
                else:
                    value = raw
            except ValueError as exc:
                raise ValueError(
                    f"{source_path.name} 第 {line_number} 行 {column} 格式错误"
                ) from exc
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"{source_path.name} 第 {line_number} 行不能包含负数")
            values.append(value)
        rows.append(tuple(values))
    return rows, keys


def create_demo_database(
    path: str | os.PathLike[str] = DEFAULT_DB_PATH,
    *,
    csv_path: str | os.PathLike[str] = DEFAULT_CSV_PATH,
    products_path: str | os.PathLike[str] = DEFAULT_PRODUCTS_PATH,
    channels_path: str | os.PathLike[str] = DEFAULT_CHANNELS_PATH,
    schema_path: str | os.PathLike[str] = DEFAULT_SCHEMA_PATH,
    overwrite: bool = True,
) -> Path:
    """从确定性 CSV 重建演示库；初始化连接与在线只读连接完全分离。"""

    database_path = Path(path).expanduser().resolve()
    source_csv = Path(csv_path).expanduser().resolve()
    source_schema = Path(schema_path).expanduser().resolve()
    if database_path.exists():
        if not overwrite:
            return database_path
        database_path.unlink()
    database_path.parent.mkdir(parents=True, exist_ok=True)

    product_rows, product_categories = _load_dimension_rows(products_path, "products")
    channel_rows, channel_names = _load_dimension_rows(channels_path, "channels")

    with source_csv.open("r", encoding="utf-8", newline="") as handle:
        source_rows = list(csv.DictReader(handle))
    required = list(TABLE_SCHEMA["orders"])
    if len(source_rows) < 30:
        raise ValueError("演示数据至少需要 30 行")
    if not source_rows or list(source_rows[0]) != required:
        raise ValueError("orders.csv 表头与内置 schema 不一致")

    rows: list[tuple[object, ...]] = []
    seen_ids: set[str] = set()
    for line_number, row in enumerate(source_rows, start=2):
        order_id = row["order_id"].strip()
        if not order_id or order_id in seen_ids:
            raise ValueError(f"CSV 第 {line_number} 行 order_id 为空或重复")
        seen_ids.add(order_id)
        try:
            quantity = int(row["quantity"])
            revenue = float(row["revenue"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"CSV 第 {line_number} 行数值格式错误") from exc
        if quantity < 0 or revenue < 0:
            raise ValueError(f"CSV 第 {line_number} 行不能包含负数")
        if row["category"] not in product_categories:
            raise ValueError(f"CSV 第 {line_number} 行 category 不在 products 维度中")
        if row["channel"] not in channel_names:
            raise ValueError(f"CSV 第 {line_number} 行 channel 不在 channels 维度中")
        rows.append(
            (
                order_id,
                row["order_date"],
                row["category"],
                row["region"],
                row["channel"],
                quantity,
                revenue,
            )
        )

    schema_sql = source_schema.read_text(encoding="utf-8")
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(schema_sql)
        for table, dimension_rows in (
            ("products", product_rows),
            ("channels", channel_rows),
        ):
            columns = list(TABLE_SCHEMA[table])
            placeholders = ", ".join("?" for _ in columns)
            connection.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                dimension_rows,
            )
        placeholders = ", ".join("?" for _ in required)
        columns = ", ".join(required)
        connection.executemany(
            f"INSERT INTO orders ({columns}) VALUES ({placeholders})",
            rows,
        )
        connection.commit()
    finally:
        connection.close()
    return database_path


def resolve_database_path(path: str | os.PathLike[str] | None = None) -> Path:
    configured = path or os.getenv("DATACHAT_DB") or DEFAULT_DB_PATH
    return Path(configured).expanduser().resolve()


def _read_only_authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    database_name: str | None,
    trigger_or_view: str | None,
) -> int:
    """SQLite 执行前的最终授权。

    词法扫描只能理解常见语法，无法覆盖 SQLite 的全部解析细节、CTE 展开和
    函数解析。因此必须在数据库层按“实际动作”授权：默认拒绝，仅放行 SELECT、
    orders / products / channels 白名单列读取及安全函数。即使攻击 SQL 绕过文本
    检查，写操作也会在这里被 SQLITE_DENY 拦截；mode=ro 与 query_only=ON 还提供
    独立兜底。
    """

    del database_name, trigger_or_view
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_RECURSIVE}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        table = (arg1 or "").casefold()
        column = (arg2 or "").casefold()
        allowed_columns = TABLE_SCHEMA.get(table)
        # COUNT(*) 等整行操作可能使用空列名；真实列仍逐项校验。
        if allowed_columns is not None and (
            not column or column in allowed_columns
        ):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION:
        function_name = (arg2 or arg1 or "").casefold()
        if function_name in ALLOWED_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    # INSERT/UPDATE/DELETE、DDL、PRAGMA、ATTACH、TRANSACTION 等默认全部拒绝。
    return sqlite3.SQLITE_DENY


class ReadOnlyDatabase:
    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        max_rows: int = 200,
        timeout_seconds: float = 2.0,
    ) -> None:
        self.path = resolve_database_path(path)
        self.guard = SQLGuard(max_rows=max_rows)
        self.timeout_seconds = timeout_seconds

    def connect_read_only(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise QueryExecutionError("数据库尚未初始化，请先运行 python demo.py。")
        uri = f"{self.path.as_uri()}?mode=ro"
        try:
            connection = sqlite3.connect(
                uri,
                uri=True,
                timeout=0.2,
                isolation_level=None,
                check_same_thread=False,
            )
            connection.execute("PRAGMA query_only = ON")
            connection.execute("PRAGMA trusted_schema = OFF")
            connection.set_authorizer(_read_only_authorizer)
            return connection
        except sqlite3.Error:
            try:
                connection.close()
            except (UnboundLocalError, sqlite3.Error):
                pass
            raise QueryExecutionError("无法以只读方式打开数据库。") from None

    def execute_guarded(self, query: GuardedQuery) -> QueryResult:
        connection = self.connect_read_only()
        deadline = time.monotonic() + self.timeout_seconds

        def progress_handler() -> int:
            # SQLite 的 timeout 参数只处理锁等待；墙钟超时必须由进度回调实现。
            return 1 if time.monotonic() >= deadline else 0

        try:
            connection.set_progress_handler(progress_handler, 500)
            cursor = connection.execute(query.sql, query.params)
            rows = cursor.fetchmany(query.max_rows + 1)
            if len(rows) > query.max_rows:
                raise QueryExecutionError(f"结果超过 {query.max_rows} 行上限，请缩小查询范围。")
            columns = [item[0] for item in (cursor.description or [])]
            return QueryResult(columns, [list(row) for row in rows], query.sql)
        except QueryExecutionError:
            raise
        except sqlite3.OperationalError as exc:
            message = str(exc).casefold()
            if time.monotonic() >= deadline or "interrupted" in message:
                raise QueryTimeoutError("查询超时，请缩小范围或简化计算。") from None
            if "not authorized" in message or "access to" in message:
                raise QueryExecutionError("数据库 authorizer 已阻止未授权操作。") from None
            if "readonly" in message or "read-only" in message:
                raise QueryExecutionError("数据库为只读模式，已阻止写操作。") from None
            if "query aborted" in message or "too many" in message:
                raise QueryExecutionError("查询超出系统限制，请缩小范围。") from None
            raise QueryExecutionError("查询无法执行，请检查 SQL 语法、列名和参数。") from None
        except sqlite3.Error:
            raise QueryExecutionError("查询执行失败，请检查查询或联系管理员。") from None
        finally:
            connection.set_progress_handler(None, 0)
            connection.close()

    def execute(
        self,
        sql: str | GuardedQuery,
        params: Iterable[object] = (),
    ) -> QueryResult:
        # 所有入口（规则、LLM、API、demo）都必须经过同一个 guard。
        guarded = sql if isinstance(sql, GuardedQuery) else self.guard.validate(sql, params)
        return self.execute_guarded(guarded)
