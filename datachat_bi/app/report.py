"""生成无外部资源、可直接打开的单文件 HTML 分析报告。"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence


def _text(value: object) -> str:
    if value is None:
        return "—"
    return html.escape(str(value), quote=True)


def _result_table(columns: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    header = "".join(f"<th scope=\"col\">{_text(column)}</th>" for column in columns)
    if rows:
        body = "".join(
            "<tr>" + "".join(f"<td>{_text(value)}</td>" for value in row) + "</tr>"
            for row in rows
        )
    else:
        body = f'<tr><td colspan="{max(1, len(columns))}" class="empty">暂无数据</td></tr>'
    return (
        '<div class="table-wrap"><table><thead><tr>'
        f"{header}</tr></thead><tbody>{body}</tbody></table></div>"
    )


def generate_html_report(
    results: Sequence[Mapping[str, object]],
    output_path: str | Path,
    *,
    title: str = "DataChat BI 电商销售分析报告",
    mode: str = "离线规则（限定明确问法）",
) -> Path:
    sections: list[str] = []
    for index, result in enumerate(results, start=1):
        question = _text(result.get("question", f"问题 {index}"))
        sql = _text(result.get("sql", ""))
        summary = _text(result.get("summary", ""))
        result_mode = _text(result.get("mode", mode))
        columns = result.get("columns", [])
        rows = result.get("rows", [])
        if not isinstance(columns, Sequence) or not isinstance(rows, Sequence):
            raise ValueError("报告结果必须包含 columns 和 rows 序列")
        table = _result_table([str(item) for item in columns], rows)  # type: ignore[arg-type]
        chart = result.get("chart")
        chart_html = f'<div class="chart">{chart}</div>' if isinstance(chart, str) and chart else ""
        row_count = len(rows)
        sections.append(
            f'''<section class="card">
  <div class="section-heading"><span class="number">{index:02d}</span><h2>{question}</h2></div>
  <p class="summary">{summary}</p>
  <div class="meta">模式：{result_mode}　·　返回 {row_count} 行</div>
  <details><summary>查看生成并实际执行的 SQL</summary><pre><code>{sql}</code></pre></details>
  {chart_html}
  {table}
</section>'''
        )

    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    document = f'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_text(title)}</title>
<style>
:root{{color-scheme:light;--ink:#0f172a;--muted:#64748b;--line:#e2e8f0;--blue:#2563eb;--paper:#f1f5f9}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font-family:Inter,"Segoe UI","Microsoft YaHei",system-ui,sans-serif;line-height:1.6}}
header{{background:linear-gradient(120deg,#0f172a,#1e3a8a);color:#fff;padding:46px max(24px,calc((100vw - 1080px)/2))}}
header .eyebrow{{font-size:12px;letter-spacing:.18em;text-transform:uppercase;color:#bfdbfe}}h1{{margin:8px 0;font-size:clamp(26px,4vw,40px);line-height:1.2}}header p{{margin:8px 0 0;color:#dbeafe}}
main{{max-width:1080px;margin:28px auto 56px;padding:0 20px}}.notice{{background:#eff6ff;border:1px solid #bfdbfe;border-radius:12px;padding:14px 18px;color:#1e40af;margin-bottom:20px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:16px;padding:24px;margin:18px 0;box-shadow:0 8px 28px rgba(15,23,42,.05)}}
.section-heading{{display:flex;align-items:flex-start;gap:12px}}.number{{flex:0 0 auto;background:#dbeafe;color:#1d4ed8;border-radius:8px;padding:2px 8px;font-weight:700;font-size:13px;margin-top:4px}}h2{{font-size:20px;line-height:1.4;margin:0}}.summary{{margin:12px 0 4px;color:#334155}}.meta{{font-size:12px;color:var(--muted);margin-bottom:14px}}
details{{margin:12px 0}}details summary{{cursor:pointer;color:#1d4ed8;font-size:13px}}pre{{white-space:pre-wrap;word-break:break-word;background:#0f172a;color:#e2e8f0;border-radius:10px;padding:16px;font:13px/1.6 ui-monospace,SFMono-Regular,Consolas,monospace}}
.chart{{border:1px solid var(--line);border-radius:14px;overflow:hidden;margin:16px 0}}.chart svg{{display:block;width:100%;height:auto}}.table-wrap{{overflow-x:auto;border:1px solid var(--line);border-radius:12px}}table{{width:100%;border-collapse:collapse;font-size:13px}}th,td{{padding:11px 14px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}}th{{background:#f8fafc;color:#475569;font-weight:650}}tbody tr:last-child td{{border-bottom:0}}.empty{{text-align:center;color:var(--muted)}}
footer{{text-align:center;color:var(--muted);font-size:12px;margin-top:28px}}@media print{{body{{background:#fff}}header{{padding:24px;background:#0f172a}}main{{margin:16px auto}}.card{{break-inside:avoid;box-shadow:none}}details{{display:none}}}}
</style>
</head>
<body>
<header><div class="eyebrow">DataChat BI · AI Portfolio</div><h1>{_text(title)}</h1><p>确定性电商演示数据 · 只读 SQLite · 内联 SVG</p></header>
<main>
<div class="notice">运行模式：{_text(mode)}。所有 SQL 均经过统一安全守护；本报告不依赖外部 CDN、脚本或浏览器存储。</div>
{''.join(sections)}
<footer>生成时间：{_text(generated_at)}　·　DataChat BI</footer>
</main>
</body>
</html>'''
    destination = Path(output_path).expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(document, encoding="utf-8")
    return destination
