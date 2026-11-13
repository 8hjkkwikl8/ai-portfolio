"""SQL 词法/结构预检。

这一层用于尽早给出稳定、安全的错误，并限制 SQL 的形状；它不是唯一防线。
真正的表、列、函数和写操作授权仍由 database.py 中的 SQLite authorizer 执行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

TABLE_SCHEMA: dict[str, dict[str, str]] = {
    "orders": {
        "order_id": "TEXT",
        "order_date": "TEXT",
        "category": "TEXT",
        "region": "TEXT",
        "channel": "TEXT",
        "quantity": "INTEGER",
        "revenue": "REAL",
    },
    "products": {
        "category": "TEXT",
        "product_line": "TEXT",
        "warranty_days": "INTEGER",
    },
    "channels": {
        "channel": "TEXT",
        "channel_type": "TEXT",
        "commission_rate": "REAL",
    },
}

# authorizer 与预检共用白名单。未列出的函数一律拒绝，而不是依赖黑名单。
ALLOWED_FUNCTIONS = frozenset(
    {
        "abs",
        "avg",
        "coalesce",
        "count",
        "date",
        "datetime",
        "dense_rank",
        "group_concat",
        "ifnull",
        "iif",
        "julianday",
        "lag",
        "lead",
        "length",
        "lower",
        "ltrim",
        "max",
        "min",
        "nullif",
        "rank",
        "round",
        "row_number",
        "rtrim",
        "strftime",
        "substr",
        "sum",
        "time",
        "total",
        "trim",
        "unixepoch",
        "upper",
    }
)

DANGEROUS_FUNCTIONS = frozenset(
    {
        "edit",
        "eval",
        "fts3_tokenizer",
        "load_extension",
        "readfile",
        "sqlar_compress",
        "sqlar_uncompress",
        "writefile",
        "zipfile",
    }
)

FORBIDDEN_KEYWORDS = frozenset(
    {
        "abort",
        "alter",
        "analyze",
        "attach",
        "begin",
        "commit",
        "create",
        "delete",
        "detach",
        "drop",
        "insert",
        "into",
        "pragma",
        "reindex",
        "release",
        "replace",
        "rollback",
        "savepoint",
        "transaction",
        "update",
        "vacuum",
    }
)

FROM_TERMINATORS = frozenset(
    {
        "except",
        "fetch",
        "group",
        "having",
        "intersect",
        "limit",
        "offset",
        "order",
        "returning",
        "union",
        "where",
        "window",
    }
)

ALIAS_STOP_WORDS = FROM_TERMINATORS | frozenset(
    {"cross", "full", "inner", "join", "left", "natural", "on", "outer", "right"}
)


class SQLGuardError(ValueError):
    """可直接展示给用户的 SQL 安全错误。"""


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    upper: str
    start: int
    end: int


@dataclass(frozen=True)
class GuardedQuery:
    sql: str
    params: tuple[object, ...]
    max_rows: int


def _reject_comments(sql: str) -> None:
    """在字符串/引用标识符之外拒绝注释，避免注释切断多语句检查。"""

    quote: str | None = None
    i = 0
    while i < len(sql):
        ch = sql[i]
        if quote:
            if quote == "[":
                if ch == "]":
                    quote = None
                i += 1
                continue
            if ch == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    i += 2
                    continue
                quote = None
            i += 1
            continue
        if ch in {"'", '"', "`", "["}:
            quote = ch
            i += 1
            continue
        if (ch == "-" and i + 1 < len(sql) and sql[i + 1] == "-") or (
            ch == "/" and i + 1 < len(sql) and sql[i + 1] == "*"
        ):
            raise SQLGuardError("不允许 SQL 注释，请提交一条清晰的只读查询。")
        i += 1


def _tokenize(sql: str) -> list[Token]:
    tokens: list[Token] = []
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "\x00" or (ord(ch) < 32 and not ch.isspace()):
            raise SQLGuardError("SQL 含有不支持的控制字符。")
        if ch in {"'", '"', "`", "["}:
            start = i
            closing = "]" if ch == "[" else ch
            i += 1
            value_chars: list[str] = []
            closed = False
            while i < len(sql):
                current = sql[i]
                if current == closing:
                    if closing != "]" and i + 1 < len(sql) and sql[i + 1] == closing:
                        value_chars.append(closing)
                        i += 2
                        continue
                    i += 1
                    closed = True
                    break
                value_chars.append(current)
                i += 1
            if not closed:
                raise SQLGuardError("SQL 中存在未闭合的字符串或标识符。")
            raw = sql[start:i]
            kind = "STRING" if ch == "'" else "IDENT"
            value = raw if kind == "STRING" else "".join(value_chars)
            tokens.append(Token(kind, value, value.upper(), start, i))
            continue
        if ch.isdigit() or (ch == "." and i + 1 < len(sql) and sql[i + 1].isdigit()):
            start = i
            if ch == ".":
                i += 1
            while i < len(sql) and sql[i].isdigit():
                i += 1
            if i < len(sql) and sql[i] == ".":
                i += 1
                while i < len(sql) and sql[i].isdigit():
                    i += 1
            if i < len(sql) and sql[i] in {"e", "E"}:
                exponent = i
                i += 1
                if i < len(sql) and sql[i] in {"+", "-"}:
                    i += 1
                digit_start = i
                while i < len(sql) and sql[i].isdigit():
                    i += 1
                if digit_start == i:
                    raise SQLGuardError(f"数字字面量格式不正确（位置 {exponent + 1}）。")
            value = sql[start:i]
            tokens.append(Token("NUMBER", value, value, start, i))
            continue
        if ch.isalpha() or ch == "_" or ord(ch) >= 128:
            start = i
            i += 1
            while i < len(sql):
                current = sql[i]
                if current.isalnum() or current in {"_", "$"} or ord(current) >= 128:
                    i += 1
                else:
                    break
            value = sql[start:i]
            tokens.append(Token("IDENT", value, value.upper(), start, i))
            continue
        if ch == "?" or (ch in {":", "@", "$"} and i + 1 < len(sql)):
            start = i
            i += 1
            if ch == "?":
                while i < len(sql) and sql[i].isdigit():
                    i += 1
            else:
                while i < len(sql) and (sql[i].isalnum() or sql[i] == "_"):
                    i += 1
            tokens.append(Token("PARAM", sql[start:i], sql[start:i], start, i))
            continue
        start = i
        i += 1
        kind = "PUNCT" if ch in {"(", ")", ",", ".", ";", "*"} else "OP"
        tokens.append(Token(kind, ch, ch, start, i))
    return tokens


def _skip_parenthesized(tokens: Sequence[Token], index: int) -> int:
    if index >= len(tokens) or tokens[index].value != "(":
        raise SQLGuardError("WITH 子句结构不完整。")
    depth = 0
    for current in range(index, len(tokens)):
        value = tokens[current].value
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
            if depth == 0:
                return current + 1
    raise SQLGuardError("SQL 括号未闭合。")


def _parse_cte_names(tokens: Sequence[Token]) -> set[str]:
    if not tokens or tokens[0].upper != "WITH":
        return set()
    names: set[str] = set()
    i = 1
    if i < len(tokens) and tokens[i].upper == "RECURSIVE":
        i += 1
    while i < len(tokens):
        token = tokens[i]
        if token.kind != "IDENT" or token.value.casefold() in FORBIDDEN_KEYWORDS:
            raise SQLGuardError("WITH 公用表表达式名称无效。")
        name = token.value.casefold()
        if name in TABLE_SCHEMA or name in names:
            raise SQLGuardError("WITH 名称重复或与受保护表冲突。")
        names.add(name)
        i += 1
        if i < len(tokens) and tokens[i].value == "(":
            i = _skip_parenthesized(tokens, i)
        if i >= len(tokens) or tokens[i].upper != "AS":
            raise SQLGuardError("WITH 公用表表达式缺少 AS。")
        i += 1
        if i < len(tokens) and tokens[i].upper == "NOT":
            i += 1
        if i < len(tokens) and tokens[i].upper == "MATERIALIZED":
            i += 1
        if i >= len(tokens) or tokens[i].value != "(":
            raise SQLGuardError("WITH 公用表表达式必须是括号中的 SELECT。")
        inner_start = i + 1
        if inner_start >= len(tokens) or tokens[inner_start].upper not in {"SELECT", "WITH"}:
            raise SQLGuardError("WITH 中只允许 SELECT，不允许隐藏写操作。")
        i = _skip_parenthesized(tokens, i)
        if i < len(tokens) and tokens[i].value == ",":
            i += 1
            continue
        break
    if i >= len(tokens) or tokens[i].upper != "SELECT":
        raise SQLGuardError("WITH 之后必须是一条 SELECT 查询。")
    return names


def _validate_parentheses(tokens: Sequence[Token]) -> None:
    depth = 0
    for token in tokens:
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
            if depth < 0:
                raise SQLGuardError("SQL 括号不匹配。")
    if depth:
        raise SQLGuardError("SQL 括号未闭合。")


def _validate_tables_and_columns(tokens: Sequence[Token], cte_names: set[str]) -> None:
    allowed_tables = set(TABLE_SCHEMA) | cte_names
    # 每个括号深度分别维护 FROM 状态，可识别逗号连接及派生表。
    states: dict[int, str] = {}
    aliases: dict[str, str] = {}
    depth = 0
    for i, token in enumerate(tokens):
        value = token.value
        if value == "(":
            if states.get(depth) == "expect_table":
                states[depth] = "in_from"  # FROM (SELECT ...) 派生表
            depth += 1
            continue
        if value == ")":
            states.pop(depth, None)
            depth -= 1
            continue
        state = states.get(depth)
        if token.upper in {"FROM", "JOIN"}:
            states[depth] = "expect_table"
            continue
        if state == "expect_table":
            if token.kind != "IDENT":
                raise SQLGuardError("FROM/JOIN 后只能是白名单表或只读子查询。")
            table_name = token.value.casefold()
            if table_name not in allowed_tables:
                raise SQLGuardError("查询包含未授权的数据表。")
            if i + 1 < len(tokens) and tokens[i + 1].value == ".":
                raise SQLGuardError("不允许使用数据库 schema 限定表名。")
            if table_name in TABLE_SCHEMA:
                # 只有白名单物理表才登记别名；CTE 与派生表不参与列归属校验，
                # 其列最终仍由 SQLite authorizer 按真实表逐列授权。
                aliases[token.value.casefold()] = table_name
                next_index = i + 1
                if next_index < len(tokens) and tokens[next_index].upper == "AS":
                    next_index += 1
                if next_index < len(tokens):
                    possible_alias = tokens[next_index]
                    if (
                        possible_alias.kind == "IDENT"
                        and possible_alias.value.casefold() not in ALIAS_STOP_WORDS
                        and possible_alias.value.casefold() not in allowed_tables
                    ):
                        aliases[possible_alias.value.casefold()] = table_name
            states[depth] = "in_from"
            continue
        if state == "in_from":
            if token.value.casefold() in FROM_TERMINATORS:
                states.pop(depth, None)
            elif value == ",":
                states[depth] = "expect_table"

    for i in range(1, len(tokens) - 1):
        if tokens[i].value != ".":
            continue
        owner = tokens[i - 1]
        column = tokens[i + 1]
        if owner.kind != "IDENT":
            continue
        table = aliases.get(owner.value.casefold())
        if table is None:
            continue
        if column.kind == "IDENT" and column.value.casefold() not in TABLE_SCHEMA[table]:
            raise SQLGuardError(f"查询包含 {table} 表中不存在或未授权的列。")


NON_FUNCTION_PAREN_WORDS = frozenset(
    {
        "and",
        "as",
        "between",
        "by",
        "case",
        "cast",
        "else",
        "except",
        "exists",
        "filter",
        "from",
        "group",
        "having",
        "in",
        "intersect",
        "join",
        "limit",
        "not",
        "on",
        "or",
        "order",
        "over",
        "partition",
        "select",
        "then",
        "union",
        "values",
        "when",
        "where",
        "with",
    }
)


def _validate_functions(tokens: Sequence[Token], cte_names: set[str]) -> None:
    for i, token in enumerate(tokens[:-1]):
        if token.kind != "IDENT" or tokens[i + 1].value != "(":
            continue
        name = token.value.casefold()
        if name in DANGEROUS_FUNCTIONS:
            raise SQLGuardError("查询包含被禁止的危险函数。")
        if name not in ALLOWED_FUNCTIONS | cte_names | NON_FUNCTION_PAREN_WORDS:
            raise SQLGuardError(f"函数 {token.value} 不在只读分析白名单中。")


def _apply_limit(sql: str, tokens: Sequence[Token], max_rows: int) -> str:
    depth = 0
    limit_indexes: list[int] = []
    for i, token in enumerate(tokens):
        if token.value == "(":
            depth += 1
        elif token.value == ")":
            depth -= 1
        elif token.upper == "LIMIT" and depth == 0:
            limit_indexes.append(i)
    if not limit_indexes:
        return f"{sql.rstrip()} LIMIT {max_rows}"
    if len(limit_indexes) > 1:
        raise SQLGuardError("主查询只能有一个 LIMIT。")
    index = limit_indexes[0]
    if index + 1 >= len(tokens):
        raise SQLGuardError("LIMIT 后需要非负整数。")
    limit_token = tokens[index + 1]
    if limit_token.kind != "NUMBER" or not limit_token.value.isdigit():
        raise SQLGuardError("LIMIT 仅支持非负整数字面量；行数上限由系统统一控制。")
    if int(limit_token.value) > max_rows:
        sql = f"{sql[:limit_token.start]}{max_rows}{sql[limit_token.end:]}"
    if index + 2 < len(tokens):
        next_token = tokens[index + 2]
        if next_token.value == ",":
            raise SQLGuardError("请使用 LIMIT n OFFSET m，不支持逗号分页语法。")
        if next_token.upper == "OFFSET":
            if index + 3 >= len(tokens):
                raise SQLGuardError("OFFSET 后需要非负整数。")
            offset = tokens[index + 3]
            if offset.kind != "NUMBER" or not offset.value.isdigit():
                raise SQLGuardError("OFFSET 仅支持非负整数字面量。")
            if index + 4 != len(tokens):
                raise SQLGuardError("LIMIT/OFFSET 后存在无法识别的内容。")
        else:
            raise SQLGuardError("LIMIT 后存在不支持的分页内容。")
    return sql


class SQLGuard:
    def __init__(self, max_rows: int = 200, max_sql_length: int = 12_000) -> None:
        if not 1 <= max_rows <= 10_000:
            raise ValueError("max_rows 必须在 1 到 10000 之间")
        self.max_rows = max_rows
        self.max_sql_length = max_sql_length

    def validate(self, sql: str, params: Iterable[object] = ()) -> GuardedQuery:
        if not isinstance(sql, str) or not sql.strip():
            raise SQLGuardError("SQL 不能为空。")
        if len(sql) > self.max_sql_length:
            raise SQLGuardError("SQL 过长，请缩小查询。")
        if isinstance(params, (str, bytes, dict)):
            raise SQLGuardError("查询参数必须使用位置参数序列。")
        clean_sql = sql.strip()
        _reject_comments(clean_sql)
        tokens = _tokenize(clean_sql)
        if not tokens:
            raise SQLGuardError("SQL 不能为空。")
        semicolons = [token for token in tokens if token.value == ";"]
        if semicolons:
            if len(semicolons) != 1 or semicolons[0] is not tokens[-1]:
                raise SQLGuardError("仅允许单条 SELECT/WITH，禁止多语句执行。")
            clean_sql = clean_sql[: semicolons[0].start].rstrip()
            tokens = tokens[:-1]
        if not tokens or tokens[0].upper not in {"SELECT", "WITH"}:
            raise SQLGuardError("仅允许单条 SELECT 或 WITH 只读查询。")
        for token in tokens:
            if token.value.casefold() in FORBIDDEN_KEYWORDS:
                raise SQLGuardError(f"检测到禁止的 SQL 操作：{token.value.upper()}。")
        _validate_parentheses(tokens)
        cte_names = _parse_cte_names(tokens)
        _validate_tables_and_columns(tokens, cte_names)
        _validate_functions(tokens, cte_names)
        limited_sql = _apply_limit(clean_sql, tokens, self.max_rows)
        return GuardedQuery(limited_sql, tuple(params), self.max_rows)


def validate_sql(sql: str, params: Iterable[object] = (), max_rows: int = 200) -> GuardedQuery:
    return SQLGuard(max_rows=max_rows).validate(sql, params)
