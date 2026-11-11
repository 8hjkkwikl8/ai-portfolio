"""明确规则的离线 planner，以及可选的 OpenAI 兼容候选 SQL planner。"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from .chart import ChartSpec
from .guard import TABLE_SCHEMA

EXAMPLE_QUESTIONS: tuple[str, ...] = (
    "总销售额是多少？",
    "按类别销售额排行",
    "按月销售趋势",
    "按地区销售额",
    "销量最高的商品类别是什么？",
    "哪个渠道销量最高？",
    "各渠道销售额对比",
)

# 离线 planner 是显式问法路由器，不冒充任意自然语言理解。只有完整命中
# 别名才会生成 SQL；如“仅看华东”等未消费条件会安全拒绝，而非悄悄忽略。
_QUESTION_ALIASES = {
    "top_category": (
        "销量最高的商品类别是什么", "销量最高的商品类别", "销售量最高的类别",
        "哪个类别销量最高", "哪一种类别销售量最多", "topcategorybyquantity",
    ),
    "top_channel": (
        "哪个渠道销量最高", "销量最高的销售渠道是什么", "销售量最多的渠道",
        "topchannelbyquantity",
    ),
    "monthly": (
        "按月销售趋势", "每月销售额趋势", "月度销售趋势", "按月营收趋势",
        "每月销售情况", "月度收入变化", "salesbymonth", "monthlysales",
        "monthlyrevenue", "monthlysalestrend",
    ),
    "category": (
        "按类别销售额排行", "各类别销售额排名", "不同类别销售额对比",
        "按商品类别收入排行", "销售额按类别排行", "salesbycategory",
        "categorysalesranking",
    ),
    "region": (
        "按地区销售额", "各地区销售额排名", "不同地区销售额对比",
        "销售额按地区排行", "salesbyregion",
    ),
    "channel": (
        "各渠道销售额对比", "按渠道销售额排行", "不同渠道收入比较",
        "销售额按渠道对比", "salesbychannel",
    ),
    "total": (
        "总销售额是多少", "总销售额", "销售总额", "总收入是多少", "总收入",
        "累计营收", "整体销售额", "totalsales", "totalrevenue", "overallrevenue",
    ),
}
_QUESTION_INTENTS = {
    alias: intent
    for intent, aliases in _QUESTION_ALIASES.items()
    for alias in aliases
}


class PlannerError(ValueError):
    """可安全展示的规划错误。"""


@dataclass(frozen=True)
class QueryPlan:
    question: str
    sql: str
    params: tuple[object, ...]
    summary: str
    chart: ChartSpec | None = None
    mode: str = "离线规则"


class Planner(Protocol):
    mode: str

    def plan(self, question: str) -> QueryPlan: ...


class RulePlanner:
    mode = "离线规则（限定明确问法）"

    def __init__(self) -> None:
        self.examples = EXAMPLE_QUESTIONS

    @staticmethod
    def _year_filter(question: str) -> tuple[str, tuple[object, ...]]:
        """年份只作为绑定参数；多年或重复年份不在离线规则能力范围内。"""

        matches = list(re.finditer(r"(?<!\d)(20\d{2})\s*年?", question))
        if len(matches) > 1:
            raise PlannerError("离线规则一次只支持一个年份筛选，请拆分问题或使用 LLM 模式。")
        if not matches:
            return "", ()
        return " WHERE substr(order_date, 1, 4) = ?", (matches[0].group(1),)

    @staticmethod
    def _canonical_question(question: str) -> str:
        text = question.lower().replace("？", "?")
        text = re.sub(r"[?。！!．.]+$", "", text)
        for prefix in ("请帮我", "查询一下", "我想知道", "请", "帮我", "告诉我", "查询", "分析一下", "分析", "看一下", "看看"):
            if text.startswith(prefix):
                text = text[len(prefix):]
                break
        return text

    def plan(self, question: str) -> QueryPlan:
        if not isinstance(question, str) or not question.strip():
            raise PlannerError("请输入一个明确的问题。")
        normalized = re.sub(r"\s+", "", question.strip())
        where, params = self._year_filter(normalized)
        normalized = re.sub(r"(?<!\d)20\d{2}年?", "", normalized)
        normalized = self._canonical_question(normalized)
        intent = _QUESTION_INTENTS.get(normalized)

        # 更具体的“销量最高”必须排在一般类别/渠道汇总之前。
        if intent == "top_category":
            sql = (
                "SELECT category, SUM(quantity) AS total_quantity, "
                "ROUND(SUM(revenue), 2) AS total_revenue FROM orders"
                f"{where} GROUP BY category ORDER BY total_quantity DESC, category ASC LIMIT 1"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按商品类别汇总销量，并返回销量最高的类别。",
                ChartSpec("bar", "category", "total_quantity", "销量最高商品类别"),
                self.mode,
            )

        if intent == "top_channel":
            sql = (
                "SELECT channel, SUM(quantity) AS total_quantity, "
                "ROUND(SUM(revenue), 2) AS total_revenue FROM orders"
                f"{where} GROUP BY channel ORDER BY total_quantity DESC, channel ASC LIMIT 1"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按销售渠道汇总销量，并返回销量最高的渠道。",
                ChartSpec("bar", "channel", "total_quantity", "销量最高销售渠道"),
                self.mode,
            )

        if intent == "monthly":
            sql = (
                "SELECT substr(order_date, 1, 7) AS month, "
                "ROUND(SUM(revenue), 2) AS total_revenue, SUM(quantity) AS total_quantity "
                f"FROM orders{where} GROUP BY month ORDER BY month ASC"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按订单月份汇总销售额与销量，并按时间升序展示趋势。",
                ChartSpec("line", "month", "total_revenue", "月度销售额趋势"),
                self.mode,
            )

        if intent == "category":
            sql = (
                "SELECT category, ROUND(SUM(revenue), 2) AS total_revenue, "
                "SUM(quantity) AS total_quantity FROM orders"
                f"{where} GROUP BY category ORDER BY total_revenue DESC, category ASC"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按商品类别汇总销售额和销量，并按销售额降序排行。",
                ChartSpec("bar", "category", "total_revenue", "各类别销售额排行"),
                self.mode,
            )

        if intent == "region":
            sql = (
                "SELECT region, ROUND(SUM(revenue), 2) AS total_revenue, "
                "SUM(quantity) AS total_quantity FROM orders"
                f"{where} GROUP BY region ORDER BY total_revenue DESC, region ASC"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按地区汇总销售额和销量，并按销售额降序排列。",
                ChartSpec("bar", "region", "total_revenue", "各地区销售额"),
                self.mode,
            )

        if intent == "channel":
            sql = (
                "SELECT channel, ROUND(SUM(revenue), 2) AS total_revenue, "
                "SUM(quantity) AS total_quantity FROM orders"
                f"{where} GROUP BY channel ORDER BY total_revenue DESC, channel ASC"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "按销售渠道汇总销售额和销量，便于横向对比。",
                ChartSpec("bar", "channel", "total_revenue", "各渠道销售额对比"),
                self.mode,
            )

        if intent == "total":
            sql = (
                "SELECT ROUND(COALESCE(SUM(revenue), 0), 2) AS total_revenue, "
                "COALESCE(SUM(quantity), 0) AS total_quantity, COUNT(*) AS order_count "
                f"FROM orders{where}"
            )
            return QueryPlan(
                question,
                sql,
                params,
                "汇总匹配订单的总销售额、总销量和订单数。",
                None,
                self.mode,
            )

        examples = "；".join(self.examples)
        raise PlannerError(
            "离线规则只支持预定义的明确分析问法，并非任意自然语言理解。"
            f"请改用示例：{examples}。如需自由问法，请配置 OpenAI 兼容 LLM；候选 SQL 仍会经过安全守护。"
        )


class OpenAICompatiblePlanner:
    mode = "OpenAI 兼容 LLM（候选 SQL + 统一守护）"

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds

    def plan(self, question: str) -> QueryPlan:
        if not question.strip():
            raise PlannerError("请输入一个明确的问题。")
        schema = ", ".join(f"{name} {kind}" for name, kind in TABLE_SCHEMA["orders"].items())
        system_prompt = (
            "你是 SQLite 只读分析 SQL 生成器。只输出 JSON 对象，不要 Markdown、解释或注释。"
            '格式为 {"sql":"SELECT ... WHERE category = ?","params":["筛选值"]}；无筛选时 params 为 []。'
            "sql 只允许一条 SELECT/WITH，只能查询 orders 表，禁止写操作、PRAGMA、ATTACH、多语句和危险函数。"
            f"orders 列：{schema}。订单日期是 YYYY-MM-DD 文本。"
            "聚合时使用明确别名，如 month、total_revenue、total_quantity；结果最多 200 行。"
            "所有来自问题的筛选值必须放入 params，并在 sql 中用 ? 占位，绝不直接拼成 SQL 字面量。"
            "LIMIT 使用非负整数字面量，不使用参数。"
        )
        payload = json.dumps(
            {
                "model": self.model,
                "temperature": 0,
                "max_tokens": 600,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question},
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json; charset=utf-8",
                "User-Agent": "DataChat-BI/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(1_000_001)
            if len(body) > 1_000_000:
                raise PlannerError("LLM 响应过大，已停止处理。")
            data = json.loads(body.decode("utf-8"))
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
        except PlannerError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError):
            raise PlannerError("LLM 服务连接失败，请检查配置或改用离线规则模式。") from None
        except (json.JSONDecodeError, UnicodeDecodeError, KeyError, IndexError, TypeError, ValueError):
            raise PlannerError("LLM 返回格式无效，未执行任何 SQL。") from None

        candidate = content.strip()
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", candidate, flags=re.IGNORECASE | re.DOTALL)
        if fence:
            candidate = fence.group(1).strip()
        try:
            structured = json.loads(candidate)
            if not isinstance(structured, dict):
                raise ValueError("not an object")
            candidate_sql = structured["sql"]
            candidate_params = structured.get("params", [])
            if not isinstance(candidate_sql, str) or not isinstance(candidate_params, list):
                raise ValueError("invalid sql or params")
            if len(candidate_params) > 32 or any(
                value is not None and not isinstance(value, (str, int, float))
                for value in candidate_params
            ):
                raise ValueError("unsafe params")
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            raise PlannerError("LLM 未返回有效的 SQL/params JSON，未执行任何 SQL。") from None
        # 不根据执行错误反复调用模型；候选 SQL 仅此一条，随后统一交给 guard。
        return QueryPlan(
            question,
            candidate_sql,
            tuple(candidate_params),
            "LLM 候选 SQL 与绑定参数经统一守护后以只读方式执行。",
            None,
            self.mode,
        )


def planner_from_environment() -> Planner:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    model = os.getenv("OPENAI_MODEL", "").strip()
    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    if api_key and model and base_url:
        return OpenAICompatiblePlanner(base_url=base_url, api_key=api_key, model=model)
    return RulePlanner()
