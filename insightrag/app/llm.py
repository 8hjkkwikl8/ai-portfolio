"""离线抽取式回答与可选 OpenAI 兼容生成，引用只能指向本次检索。"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
from typing import Iterable, Protocol
from urllib import error, request

from .retrieval import SearchResult, tokenize


_CITATION_RE = re.compile(r"\[\s*(\d+)\s*\]")
_CITATION_LIKE_RE = re.compile(
    r"\[(?:\s*[+-]?\d+\s*(?:[,，;；]\s*)?)+\]"
)
_GENERIC_BIGRAMS = {
    "公司", "企业", "员工", "我们", "你们", "什么", "怎么", "如何", "是否",
    "可以", "需要", "请问", "一下", "哪些", "制度", "政策", "规定", "流程",
    "申请", "办理", "多少", "进行", "相关", "以及", "时候",
}
_GENERIC_ENGLISH = {
    "the", "and", "for", "what", "how", "can", "could", "please", "about",
    "company", "employee", "policy", "rules", "with", "from", "that", "this",
}


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "LLMConfig":
        return cls(
            base_url=os.environ.get("OPENAI_BASE_URL", "").strip(),
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            model=os.environ.get("OPENAI_MODEL", "").strip(),
        )

    @property
    def configured(self) -> bool:
        # 本地兼容服务可不设 key；未明确提供地址和模型时绝不联网。
        return bool(self.base_url and self.model)


class GenerationError(RuntimeError):
    pass


class Generator(Protocol):
    def generate(self, question: str, results: list[SearchResult]) -> str: ...


class OpenAICompatibleClient:
    """仅用 urllib 调用 /chat/completions，不引入 SDK。"""

    def __init__(self, config: LLMConfig) -> None:
        if not config.configured:
            raise ValueError("OPENAI_BASE_URL 与 OPENAI_MODEL 尚未配置")
        self.config = config

    def generate(self, question: str, results: list[SearchResult]) -> str:
        evidence = "\n\n".join(
            f"[{item.citation}] source={item.source} chunk={item.chunk_id}\n{item.text}"
            for item in results
        )
        payload = {
            "model": self.config.model,
            "temperature": 0,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是企业知识库问答助手。只能依据给定证据回答，证据不足就明确说不知道。"
                        "每个事实句使用证据对应的 [数字] 引用，不得创造新来源或编号。"
                    ),
                },
                {
                    "role": "user",
                    "content": f"问题：{question}\n\n证据：\n{evidence}",
                },
            ],
        }
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        endpoint = self.config.base_url.rstrip("/") + "/chat/completions"
        http_request = request.Request(
            endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with request.urlopen(
                http_request, timeout=self.config.timeout_seconds
            ) as response:
                body = json.loads(response.read().decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
        except (error.URLError, TimeoutError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise GenerationError(f"OpenAI 兼容接口调用失败：{exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise GenerationError("OpenAI 兼容接口未返回文本")
        return content.strip()


def extract_citations(text: str) -> list[int]:
    return [int(match) for match in _CITATION_RE.findall(text)]


def sanitize_citations(text: str, valid_citations: Iterable[int]) -> str:
    """清理数字引用；列表拆成单编号，越界、零值和负值一律删除。"""
    valid = set(valid_citations)

    def replace(match: re.Match[str]) -> str:
        numbers = re.findall(r"[+-]?\d+", match.group(0))
        return "".join(
            f"[{citation}]"
            for citation in (int(number) for number in numbers)
            if citation in valid
        )

    cleaned = _CITATION_LIKE_RE.sub(replace, text)
    return re.sub(r"[ \t]{2,}", " ", cleaned).strip()


def _meaningful_tokens(text: str) -> set[str]:
    tokens = set(tokenize(text))
    meaningful = {
        token
        for token in tokens
        if (
            token.startswith("zh2:")
            and token[4:] not in _GENERIC_BIGRAMS
        )
        or (
            token.startswith("en:")
            and len(token[3:]) >= 2
            and token[3:] not in _GENERIC_ENGLISH
        )
    }
    # 单字中文查询没有二元组，退回单字匹配；长查询不采用高噪声单字。
    if not meaningful and len(text.strip()) <= 2:
        meaningful = {token for token in tokens if token.startswith("zh:")}
    return meaningful


def _sentences(text: str) -> list[str]:
    pieces = re.split(r"(?<=[。！？!?；;])|[\r\n]+|(?<=[.!?])\s+", text)
    return [piece.strip() for piece in pieces if piece and piece.strip()]


def _grounded_generated_text(text: str) -> str:
    """只保留带合法引用编号的句子，并把句号后的引用归还前一句。

    这是引用结构约束，不是自然语言推理：它不能证明句子与证据语义等价，
    但能阻止模型用一个合法编号给整段无依据文本“背书”。
    """
    segments: list[str] = []
    current = ""
    for piece in _sentences(text):
        leading = re.match(r"^((?:\[\d+\]\s*)+)(.*)$", piece)
        if leading and current:
            current += leading.group(1)
            segments.append(current.strip())
            current = leading.group(2).strip()
        else:
            if current:
                segments.append(current.strip())
            current = piece
    if current:
        segments.append(current.strip())
    return "\n".join(
        segment for segment in segments if extract_citations(segment)
    )


_HEADING_RE = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|第[一二三四五六七八九十]+[章节])")
_FACT_RE = re.compile(
    r"(?:\d+(?:\.\d+)?|[零一二三四五六七八九十百千万]+)(?:元|天|点|小时|分钟|工作日|自然日|%)"
)
_QUERY_EXPANSIONS = {
    # 可解释的规则同义词只补足常见制度用语，不冒充神经语义匹配。
    "标准": ("限额", "额度"),
    "多久": ("时限", "日内", "工作日", "分钟", "小时"),
    "时限": ("日内", "工作日", "分钟", "小时"),
}


def _is_heading(sentence: str) -> bool:
    return len(sentence) <= 40 and bool(_HEADING_RE.match(sentence))


def _no_evidence() -> dict[str, object]:
    return {
        "answer": "我不知道：当前知识库中没有足以回答该问题的证据。请补充相关文档或调整问题。",
        "mode": "no_evidence",
        "mode_label": "无证据",
        "sources": [],
        "warning": None,
    }


def _offline_answer(
    question: str, results: list[SearchResult], max_sentences: int = 3
) -> dict[str, object]:
    if not results:
        return _no_evidence()
    original_tokens = _meaningful_tokens(question)
    if not original_tokens:
        return _no_evidence()
    query_tokens = set(original_tokens)
    for trigger, alternatives in _QUERY_EXPANSIONS.items():
        if trigger in question:
            for alternative in alternatives:
                query_tokens.update(_meaningful_tokens(alternative))

    # 每个候选为 (加权重合度, 原句位置, 句子)。数值事实略有加分，
    # 使“标准/多久”类问题优先落到限额、日期等直接答案，而非相邻描述句。
    candidates: list[tuple[SearchResult, list[tuple[float, int, str]]]] = []
    minimum_overlap = 1 if len(original_tokens) <= 3 else 2
    for item in results[: max(5, max_sentences)]:
        ranked: list[tuple[float, int, str]] = []
        for position, sentence in enumerate(_sentences(item.text)):
            if _is_heading(sentence):
                continue
            overlap = len(query_tokens & _meaningful_tokens(sentence))
            coverage = overlap / len(original_tokens)
            if overlap < minimum_overlap or coverage < 0.1:
                continue
            fact_bonus = 0.15 * min(3, len(_FACT_RE.findall(sentence)))
            ranked.append((overlap + fact_bonus, position, sentence))
        ranked.sort(key=lambda candidate: (-candidate[0], candidate[1]))
        if ranked:
            candidates.append((item, ranked))

    selected: list[tuple[SearchResult, str]] = []
    # 先覆盖不同检索块，避免同一块挤掉多意图问题所需的其他证据。
    for item, ranked in candidates:
        selected.append((item, ranked[0][2]))
        if len(selected) >= max_sentences:
            break
    # 仍有名额时再补同一块的次优句，保留必要的上下文。
    if len(selected) < max_sentences:
        for item, ranked in candidates:
            for _, _, sentence in ranked[1:]:
                selected.append((item, sentence))
                if len(selected) >= max_sentences:
                    break
            if len(selected) >= max_sentences:
                break

    if not selected:
        return _no_evidence()
    lines = ["离线摘要："]
    lines.extend(f"{sentence} [{item.citation}]" for item, sentence in selected)
    sources: list[dict[str, object]] = []
    seen_citations: set[int] = set()
    for item, _ in selected:
        if item.citation not in seen_citations:
            seen_citations.add(item.citation)
            sources.append(item.to_dict())
    return {
        "answer": "\n".join(lines),
        "mode": "offline",
        "mode_label": "离线摘要",
        "sources": sources,
        "warning": None,
    }


def answer_question(
    question: str,
    results: list[SearchResult],
    *,
    allow_online: bool = False,
    config: LLMConfig | None = None,
    client: Generator | None = None,
) -> dict[str, object]:
    """默认离线，确保无密钥、无网络时结果确定且企业文本不会外发。"""
    # 先确认本地证据能支持问题，避免把弱相关结果交给生成模型圆答。
    offline = _offline_answer(question, results)
    if offline["mode"] == "no_evidence":
        return offline

    config = config or LLMConfig.from_env()
    if not allow_online:
        return offline
    if not config.configured and client is None:
        offline["warning"] = "在线模型未配置，已使用离线摘要。"
        return offline

    try:
        generator = client or OpenAICompatibleClient(config)
        generated = generator.generate(question, results).strip()
    except (GenerationError, ValueError) as exc:
        offline["warning"] = f"在线生成不可用，已回退离线摘要：{exc}"
        return offline

    valid_citations = {item.citation for item in results}
    sanitized = sanitize_citations(generated, valid_citations)
    cleaned = _grounded_generated_text(sanitized)
    used_citations = set(extract_citations(cleaned))
    # 校验边界：编号合法不等于句意忠实。无引用句、只剩无效编号的句子均丢弃；
    # 若没有句子通过结构校验，则整体回退到可抽取复核的离线摘要。
    if not used_citations:
        offline["warning"] = "在线回答未通过逐句引用校验，已回退离线摘要。"
        return offline

    # sources 不采用模型返回内容，而是从当前 results 重新构建，杜绝伪造来源。
    sources = [
        item.to_dict() for item in results if item.citation in used_citations
    ]
    return {
        "answer": cleaned,
        "mode": "online",
        "mode_label": "OpenAI 兼容生成",
        "sources": sources,
        "warning": "仅保留带合法引用编号的句子；编号合法不代表语义忠实度已验证。",
    }
