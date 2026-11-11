"""无外部依赖、全动态内容转义的中文 HTML 报告。"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Mapping

from .runner import METRIC_NAMES

METRIC_LABELS = {"exact_match": "完全匹配", "contains": "包含期望", "regex": "正则通过"}

CSS = """
:root{--ink:#172033;--muted:#657089;--line:#e4e9f2;--paper:#f5f7fb;--card:#fff;--navy:#14213d;--blue:#3767d6;--green:#13795b;--red:#b43b4b;--amber:#a05a00}*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif}.hero{background:linear-gradient(120deg,#101b34,#1e3764);color:#fff;padding:38px max(24px,calc((100vw - 1240px)/2)) 32px}.eyebrow{font-size:12px;letter-spacing:.16em;text-transform:uppercase;color:#a9c3ff;font-weight:700}.hero h1{font-size:30px;line-height:1.25;margin:8px 0 10px}.hero p{margin:0;color:#d7e2f7;max-width:850px}.wrap{max-width:1240px;margin:0 auto;padding:26px 24px 48px}.notice{border:1px solid #cfdaf1;background:#eef3ff;border-radius:12px;padding:12px 16px;margin-bottom:22px;color:#314a7d}.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:20px;box-shadow:0 5px 20px rgba(20,33,61,.035)}.metric-label{color:var(--muted);font-size:13px}.metric-value{font-size:30px;font-weight:750;margin:4px 0}.metric-sub{font-size:12px;color:var(--muted)}section{margin-top:26px}h2{font-size:20px;margin:0 0 12px}h3{font-size:15px;margin:0 0 8px}.meta{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 24px}.meta-row{display:grid;grid-template-columns:120px minmax(0,1fr);gap:12px;padding:9px 0;border-bottom:1px solid var(--line)}.meta-row span:first-child{color:var(--muted)}code{font-family:Consolas,"SFMono-Regular",monospace;font-size:12px;overflow-wrap:anywhere}.table-wrap{overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:#fff}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:11px 14px;border-bottom:1px solid var(--line);vertical-align:top}th{background:#f0f3f9;color:#4d5a72;font-size:12px;letter-spacing:.02em}tr:last-child td{border-bottom:0}.num{text-align:right;font-variant-numeric:tabular-nums}.delta-pos{color:var(--green);font-weight:700}.delta-neg{color:var(--red);font-weight:700}.delta-zero{color:var(--muted)}.badge{display:inline-block;padding:2px 8px;border-radius:999px;background:#edf1f8;color:#4f5e76;font-size:12px;margin:1px 4px 1px 0}.badge.ok{background:#e4f5ef;color:var(--green)}.badge.fail{background:#fbeaec;color:var(--red)}.badge.mock{background:#fff3dd;color:var(--amber)}.item{margin-bottom:14px;background:#fff;border:1px solid var(--line);border-radius:13px;overflow:hidden}.item summary{cursor:pointer;padding:15px 18px;list-style:none}.item summary::-webkit-details-marker{display:none}.item summary::after{content:"＋";float:right;color:var(--muted)}.item[open] summary::after{content:"－"}.item-title{font-weight:700;margin-right:10px}.item-body{border-top:1px solid var(--line);padding:18px}.io-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.io{min-width:0}.io.full{grid-column:1/-1}pre{white-space:pre-wrap;overflow-wrap:anywhere;word-break:break-word;background:#f7f9fc;border:1px solid var(--line);border-radius:9px;padding:12px;margin:5px 0 0;min-height:44px;font:12px/1.65 Consolas,"Microsoft YaHei",monospace}.error{background:#fff2f3;border-color:#f1ccd1;color:#8e2837}.mini-metrics{display:flex;gap:14px;flex-wrap:wrap;margin-top:10px;color:var(--muted);font-size:12px}.footer{color:var(--muted);font-size:12px;margin-top:28px;text-align:center}@media(max-width:800px){.grid,.io-grid,.meta{grid-template-columns:1fr}.io.full{grid-column:auto}.hero h1{font-size:25px}.meta-row{grid-template-columns:100px minmax(0,1fr)}}@media print{body{background:#fff}.hero{padding:24px;color:#111;background:#fff;border-bottom:2px solid #111}.hero p,.eyebrow{color:#444}.wrap{padding:18px 0}.card,.item{box-shadow:none;break-inside:avoid}.item summary::after{display:none}}
"""


def _e(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return html.escape(str(value), quote=True)


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _delta(value: float | None) -> str:
    if value is None:
        return '<span class="delta-zero">—</span>'
    css = "delta-pos" if value > 0 else "delta-neg" if value < 0 else "delta-zero"
    return f'<span class="{css}">{value * 100:+.1f} pp</span>'


def _layout(title: str, subtitle: str, content: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        f"<title>{_e(title)}</title><style>{CSS}</style></head><body>"
        f"<header class=\"hero\"><div class=\"eyebrow\">PromptLens · Reproducible Evaluation</div>"
        f"<h1>{_e(title)}</h1><p>{_e(subtitle)}</p></header><main class=\"wrap\">{content}</main>"
        "<footer class=\"footer\">PromptLens · 单文件离线报告 · 所有指标由保存的输出生成</footer></body></html>"
    )


def _metadata_rows(rows: list[tuple[str, Any]]) -> str:
    return '<div class="meta">' + "".join(
        f'<div class="meta-row"><span>{_e(label)}</span><div>{value if is_html else _e(value)}</div></div>'
        for label, value, is_html in rows
    ) + "</div>"


def _model_notice(run: Mapping[str, Any]) -> str:
    model = run["manifest"]["model"]
    if model.get("is_mock"):
        return (
            '<div class="notice"><strong>离线流水线演示，不是模型质量结论：</strong>'
            '输出由确定性规则 mock provider 生成，未调用真实模型。这里的指标差异只能验证版本注册、'
            '批量运行、计分和报告流程，不构成真实模型或提示词质量提升证据；请在独立留出集上使用真实 API 复测。'
            'token 数为字符启发式估算，不是 tokenizer 精确计数。</div>'
        )
    return '<div class="notice"><strong>API 实验：</strong>结果来自 OpenAI 兼容接口；仍需独立留出集支持质量结论，token 数为字符启发式估算。</div>'


def _comparison_report(data: Mapping[str, Any]) -> str:
    baseline = data["baseline"]
    candidate = data["candidate"]
    cards = []
    for metric in METRIC_NAMES:
        before = data["summary"]["baseline"][metric]["mean"]
        after = data["summary"]["candidate"][metric]["mean"]
        cards.append(
            f'<div class="card"><div class="metric-label">{_e(METRIC_LABELS[metric])}</div>'
            f'<div class="metric-value">{_e(_pct(after))}</div>'
            f'<div class="metric-sub">基线 {_e(_pct(before))} · {_delta(data["summary"]["delta"][metric])}</div></div>'
        )
    rows = []
    for metric in METRIC_NAMES:
        before_info = data["summary"]["baseline"][metric]
        after_info = data["summary"]["candidate"][metric]
        rows.append(
            f'<tr><td>{_e(METRIC_LABELS[metric])}<br><code>{_e(metric)}</code></td>'
            f'<td class="num">{_e(_pct(before_info["mean"]))}</td>'
            f'<td class="num">{_e(_pct(after_info["mean"]))}</td>'
            f'<td class="num">{_delta(data["summary"]["delta"][metric])}</td>'
            f'<td class="num">{_e(after_info["evaluated"])}</td></tr>'
        )
    summary_table = (
        '<div class="table-wrap"><table><thead><tr><th>指标</th><th class="num">基线</th>'
        '<th class="num">候选</th><th class="num">Delta（候选－基线）</th><th class="num">候选计分样本</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div>'
    )
    tag_rows = []
    for tag, info in data["by_tag"].items():
        for index, metric in enumerate(METRIC_NAMES):
            tag_rows.append(
                f'<tr><td>{_e(tag) if index == 0 else ""}</td><td>{_e(METRIC_LABELS[metric])}</td>'
                f'<td class="num">{_e(_pct(info["baseline"][metric]["mean"]))}</td>'
                f'<td class="num">{_e(_pct(info["candidate"][metric]["mean"]))}</td>'
                f'<td class="num">{_delta(info["delta"][metric])}</td><td class="num">{_e(info["count"])}</td></tr>'
            )
    tag_table = (
        '<div class="table-wrap"><table><thead><tr><th>标签</th><th>指标</th><th class="num">基线</th>'
        '<th class="num">候选</th><th class="num">Delta</th><th class="num">样本数</th></tr></thead>'
        f'<tbody>{"".join(tag_rows)}</tbody></table></div>'
    )
    details = []
    for index, record in enumerate(data["records"], start=1):
        tags = "".join(f'<span class="badge">{_e(tag)}</span>' for tag in record.get("tags", []))
        baseline_output = record["baseline"]["output"]
        candidate_output = record["candidate"]["output"]
        baseline_status = '<span class="badge ok">成功</span>' if record["baseline"]["error"] is None else '<span class="badge fail">失败</span>'
        candidate_status = '<span class="badge ok">成功</span>' if record["candidate"]["error"] is None else '<span class="badge fail">失败</span>'
        baseline_error = (
            f'<pre class="error">{_e(record["baseline"]["error"])}</pre>' if record["baseline"]["error"] else ""
        )
        candidate_error = (
            f'<pre class="error">{_e(record["candidate"]["error"])}</pre>' if record["candidate"]["error"] else ""
        )
        delta_text = " · ".join(
            f"{METRIC_LABELS[metric]} {_pct(record['delta'][metric]) if record['delta'][metric] is not None else '—'}"
            for metric in METRIC_NAMES
        )
        details.append(
            f'<details class="item"><summary><span class="item-title">{index:02d} · {_e(record["id"])}</span>{tags}</summary>'
            '<div class="item-body"><div class="io-grid">'
            f'<div class="io full"><h3>输入</h3><pre>{_e(record["input"])}</pre></div>'
            f'<div class="io full"><h3>期望输出</h3><pre>{_e(record["expected"])}</pre></div>'
            f'<div class="io"><h3>基线输出 {baseline_status}</h3><pre>{_e(baseline_output) if baseline_output is not None else "（无输出）"}</pre>{baseline_error}</div>'
            f'<div class="io"><h3>候选输出 {candidate_status}</h3><pre>{_e(candidate_output) if candidate_output is not None else "（无输出）"}</pre>{candidate_error}</div>'
            f'</div><div class="mini-metrics">逐条 Delta：{_e(delta_text)}</div></div></details>'
        )
    metadata = _metadata_rows(
        [
            ("比较 ID", data["comparison_id"], False),
            ("生成时间", data["created_at"], False),
            ("基线实验", f"{baseline['name']} / {baseline['run_id']}", False),
            ("候选实验", f"{candidate['name']} / {candidate['run_id']}", False),
            ("基线提示词", baseline["manifest"]["prompt"]["version_id"], False),
            ("候选提示词", candidate["manifest"]["prompt"]["version_id"], False),
            ("模型 / 提供器", f"{candidate['manifest']['model']['name']} / {candidate['manifest']['model']['provider']}", False),
            ("数据 SHA256", data["dataset_sha256"], False),
            ("执行策略", json.dumps(candidate["manifest"]["execution"], ensure_ascii=False), False),
            ("Token 口径", "ceil(Unicode 字符数 / 4)，仅为估算", False),
        ]
    )
    content = (
        _model_notice(candidate)
        + f'<div class="grid">{"".join(cards)}</div>'
        + f'<section><h2>运行元数据</h2><div class="card">{metadata}</div></section>'
        + f'<section><h2>指标汇总</h2>{summary_table}</section>'
        + f'<section><h2>按标签汇总</h2>{tag_table}</section>'
        + f'<section><h2>逐条结果（{len(details)} 条）</h2>{"".join(details)}</section>'
    )
    return _layout(
        "提示词实验对比报告",
        f"{baseline['name']} → {candidate['name']} · 固定数据与模型参数，审计已保存输出差异",
        content,
    )


def _single_report(data: Mapping[str, Any]) -> str:
    cards = []
    for metric in METRIC_NAMES:
        info = data["summary"]["metrics"][metric]
        cards.append(
            f'<div class="card"><div class="metric-label">{_e(METRIC_LABELS[metric])}</div>'
            f'<div class="metric-value">{_e(_pct(info["mean"]))}</div>'
            f'<div class="metric-sub">{_e(info["correct"])} / {_e(info["evaluated"])} 条通过</div></div>'
        )
    metadata = _metadata_rows(
        [
            ("运行 ID", data["run_id"], False),
            ("实验名称", data["name"], False),
            ("生成时间", data["created_at"], False),
            ("提示词版本", data["manifest"]["prompt"]["version_id"], False),
            ("数据 SHA256", data["manifest"]["dataset"]["sha256"], False),
            ("模型参数", json.dumps(data["manifest"]["model"], ensure_ascii=False), False),
            ("执行策略", json.dumps(data["manifest"]["execution"], ensure_ascii=False), False),
            ("Token 口径", "ceil(Unicode 字符数 / 4)，仅为估算", False),
        ]
    )
    details = []
    for index, record in enumerate(data["records"], start=1):
        tags = "".join(f'<span class="badge">{_e(tag)}</span>' for tag in record.get("tags", []))
        status = '<span class="badge ok">成功</span>' if record["error"] is None else '<span class="badge fail">失败</span>'
        output = _e(record["output"]) if record["output"] is not None else "（无输出）"
        error = f'<pre class="error">{_e(record["error"])}</pre>' if record["error"] else ""
        metrics = " · ".join(
            f"{METRIC_LABELS[name]} {_pct(record['metrics'][name])}" for name in METRIC_NAMES
        )
        details.append(
            f'<details class="item"><summary><span class="item-title">{index:02d} · {_e(record["id"])}</span>{tags}{status}</summary>'
            f'<div class="item-body"><h3>输入</h3><pre>{_e(record["input"])}</pre>'
            f'<h3>期望输出</h3><pre>{_e(record["expected"])}</pre><h3>实际输出</h3><pre>{output}</pre>{error}'
            f'<div class="mini-metrics">{_e(metrics)}</div></div></details>'
        )
    content = (
        _model_notice(data)
        + f'<div class="grid">{"".join(cards)}</div>'
        + f'<section><h2>运行元数据</h2><div class="card">{metadata}</div></section>'
        + f'<section><h2>逐条结果（{len(details)} 条）</h2>{"".join(details)}</section>'
    )
    return _layout("提示词实验运行报告", f"{data['name']} · 可审计的逐条输入、输出与指标", content)


def render_html_report(data: Mapping[str, Any]) -> str:
    if data.get("type") == "promptlens_comparison":
        return _comparison_report(data)
    if data.get("type") == "promptlens_run":
        return _single_report(data)
    raise ValueError("报告输入必须是 PromptLens 运行或比较结果")


def write_html_report(data: Mapping[str, Any] | str | Path, output_path: str | Path) -> Path:
    if isinstance(data, (str, Path)):
        data = json.loads(Path(data).read_text(encoding="utf-8"))
    rendered = render_html_report(data)
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(rendered, encoding="utf-8", newline="\n")
    return destination
