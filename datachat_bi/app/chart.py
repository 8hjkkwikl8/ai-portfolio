"""仅使用标准库生成无脚本、无外部依赖的内联 SVG。"""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from typing import Sequence


@dataclass(frozen=True)
class ChartSpec:
    kind: str
    x_column: str
    y_column: str
    title: str


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _number(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("布尔值不能作为图表数值")
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError("图表不支持 NaN 或无穷大")
    return number


def _format_number(value: float) -> str:
    if abs(value) >= 1000:
        return f"{value:,.0f}"
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _clean_series(labels: Sequence[object], values: Sequence[object]) -> tuple[list[str], list[float]]:
    if len(labels) != len(values):
        raise ValueError("图表标签与数值长度不一致")
    clean_labels: list[str] = []
    clean_values: list[float] = []
    for label, value in zip(labels, values):
        if value is None:
            continue
        clean_labels.append("" if label is None else str(label))
        clean_values.append(_number(value))
    return clean_labels, clean_values


def _empty_svg(title: str, width: int, height: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" '
        f'role="img" aria-label="{_escape(title)}">'
        f'<rect width="100%" height="100%" rx="14" fill="#f8fafc"/>'
        f'<text x="{width / 2}" y="{height / 2}" text-anchor="middle" '
        'fill="#64748b" font-family="system-ui, sans-serif" font-size="16">暂无可绘制数据</text></svg>'
    )


def _frame(title: str, width: int, height: int, content: Sequence[str]) -> str:
    return "".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" ',
            f'role="img" aria-label="{_escape(title)}">',
            '<rect width="100%" height="100%" rx="14" fill="#ffffff"/>',
            f'<text x="24" y="31" fill="#0f172a" font-family="system-ui, sans-serif" ',
            f'font-size="17" font-weight="650">{_escape(title)}</text>',
            *content,
            "</svg>",
        ]
    )


def _grid(
    minimum: float,
    maximum: float,
    left: float,
    top: float,
    plot_width: float,
    plot_height: float,
) -> tuple[list[str], float]:
    span = maximum - minimum
    items: list[str] = []
    for step in range(5):
        value = minimum + span * step / 4
        y = top + plot_height - plot_height * step / 4
        items.append(
            f'<line x1="{left:.2f}" y1="{y:.2f}" x2="{left + plot_width:.2f}" y2="{y:.2f}" '
            'stroke="#e2e8f0" stroke-width="1"/>'
        )
        items.append(
            f'<text x="{left - 10:.2f}" y="{y + 4:.2f}" text-anchor="end" fill="#64748b" '
            'font-family="system-ui, sans-serif" font-size="11">'
            f'{_escape(_format_number(value))}</text>'
        )
    return items, span


def bar_chart(
    labels: Sequence[object],
    values: Sequence[object],
    title: str = "柱状图",
    *,
    width: int = 760,
    height: int = 360,
) -> str:
    labels_text, numbers = _clean_series(labels, values)
    if not numbers:
        return _empty_svg(title, width, height)
    left, right, top, bottom = 74.0, 24.0, 54.0, 76.0
    plot_width, plot_height = width - left - right, height - top - bottom
    minimum, maximum = min(0.0, min(numbers)), max(0.0, max(numbers))
    if minimum == maximum:
        maximum = minimum + 1.0
    content, span = _grid(minimum, maximum, left, top, plot_width, plot_height)
    zero_y = top + (maximum / span) * plot_height
    slot = plot_width / len(numbers)
    bar_width = max(2.0, min(64.0, slot * 0.64))
    for index, (label, value) in enumerate(zip(labels_text, numbers)):
        center = left + slot * (index + 0.5)
        value_y = top + ((maximum - value) / span) * plot_height
        rect_y = min(value_y, zero_y)
        rect_height = max(1.0, abs(zero_y - value_y))
        color = "#2563eb" if value >= 0 else "#dc2626"
        content.append(
            f'<rect x="{center - bar_width / 2:.2f}" y="{rect_y:.2f}" width="{bar_width:.2f}" '
            f'height="{rect_height:.2f}" rx="4" fill="{color}">'
            f'<title>{_escape(label)}: {_escape(_format_number(value))}</title></rect>'
        )
        short_label = label if len(label) <= 12 else f"{label[:11]}…"
        content.append(
            f'<text x="{center:.2f}" y="{height - 46:.2f}" text-anchor="middle" fill="#475569" '
            'font-family="system-ui, sans-serif" font-size="11">'
            f'<title>{_escape(label)}</title>{_escape(short_label)}</text>'
        )
        if len(numbers) <= 16:
            label_y = rect_y - 7 if value >= 0 else rect_y + rect_height + 14
            content.append(
                f'<text x="{center:.2f}" y="{label_y:.2f}" text-anchor="middle" fill="#334155" '
                'font-family="system-ui, sans-serif" font-size="10">'
                f'{_escape(_format_number(value))}</text>'
            )
    content.append(
        f'<line x1="{left:.2f}" y1="{zero_y:.2f}" x2="{left + plot_width:.2f}" y2="{zero_y:.2f}" '
        'stroke="#94a3b8" stroke-width="1.2"/>'
    )
    return _frame(title, width, height, content)


def line_chart(
    labels: Sequence[object],
    values: Sequence[object],
    title: str = "折线图",
    *,
    width: int = 760,
    height: int = 360,
) -> str:
    labels_text, numbers = _clean_series(labels, values)
    if not numbers:
        return _empty_svg(title, width, height)
    left, right, top, bottom = 74.0, 28.0, 54.0, 70.0
    plot_width, plot_height = width - left - right, height - top - bottom
    minimum, maximum = min(0.0, min(numbers)), max(0.0, max(numbers))
    if minimum == maximum:
        maximum = minimum + 1.0
    content, span = _grid(minimum, maximum, left, top, plot_width, plot_height)
    points: list[tuple[float, float]] = []
    denominator = max(1, len(numbers) - 1)
    for index, value in enumerate(numbers):
        x = left + plot_width * index / denominator
        y = top + ((maximum - value) / span) * plot_height
        points.append((x, y))
    point_text = " ".join(f"{x:.2f},{y:.2f}" for x, y in points)
    content.append(
        f'<polyline points="{point_text}" fill="none" stroke="#2563eb" stroke-width="3" '
        'stroke-linecap="round" stroke-linejoin="round"/>'
    )
    label_interval = max(1, math.ceil(len(numbers) / 12))
    for index, ((x, y), label, value) in enumerate(zip(points, labels_text, numbers)):
        content.append(
            f'<circle cx="{x:.2f}" cy="{y:.2f}" r="4" fill="#ffffff" stroke="#2563eb" stroke-width="2">'
            f'<title>{_escape(label)}: {_escape(_format_number(value))}</title></circle>'
        )
        if index % label_interval == 0 or index == len(numbers) - 1:
            short_label = label if len(label) <= 12 else f"{label[:11]}…"
            content.append(
                f'<text x="{x:.2f}" y="{height - 39:.2f}" text-anchor="middle" fill="#475569" '
                'font-family="system-ui, sans-serif" font-size="11">'
                f'{_escape(short_label)}</text>'
            )
    return _frame(title, width, height, content)


def build_chart(spec: ChartSpec, columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str | None:
    try:
        x_index = columns.index(spec.x_column)
        y_index = columns.index(spec.y_column)
    except ValueError:
        return None
    labels = [row[x_index] for row in rows]
    values = [row[y_index] for row in rows]
    if spec.kind == "bar":
        return bar_chart(labels, values, spec.title)
    if spec.kind == "line":
        return line_chart(labels, values, spec.title)
    raise ValueError("图表类型仅支持 bar 或 line")


def infer_chart(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str | None:
    """为 LLM 结果提供保守推断；无法明确识别时不画图。"""

    if len(columns) < 2 or not 2 <= len(rows) <= 30:
        return None
    if not all(isinstance(row[0], str) for row in rows):
        return None
    try:
        for row in rows:
            _number(row[1])
    except (TypeError, ValueError):
        return None
    x_name = columns[0]
    kind = "line" if "month" in x_name.casefold() or "月" in x_name else "bar"
    return build_chart(ChartSpec(kind, x_name, columns[1], "查询结果图表"), columns, rows)
