"""保留原文坐标的确定性分块：不做改写，文本可由 start/end 精确回放。"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class TextChunk:
    source: str
    chunk_id: str
    text: str
    start: int
    end: int
    ordinal: int


_BOUNDARY_RE = re.compile(r"[。！？!?；;\n]")


def chunk_text(
    text: str,
    source: str,
    chunk_size: int = 360,
    overlap: int = 60,
) -> list[TextChunk]:
    """按字符窗口分块，并尽量在中文句末或换行处结束。

    start/end 是原文 Python 字符串的半开区间字符偏移，因此始终满足
    ``text == original[start:end]``。空白块不入库，但不改变其他块坐标。
    """
    if not isinstance(text, str):
        raise TypeError("text 必须是字符串")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source 不能为空")
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须大于 0")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap 必须满足 0 <= overlap < chunk_size")
    if not text:
        return []

    chunks: list[TextChunk] = []
    length = len(text)
    start = 0
    while start < length:
        hard_end = min(start + chunk_size, length)
        end = hard_end
        if hard_end < length:
            # 不在窗口前 40% 内切分，避免大量极短块。
            earliest = start + max(1, int(chunk_size * 0.6))
            for match in _BOUNDARY_RE.finditer(text, earliest, hard_end):
                end = match.end()

        segment = text[start:end]
        if segment.strip():
            ordinal = len(chunks)
            chunks.append(
                TextChunk(
                    source=source,
                    chunk_id=f"chunk-{ordinal:04d}",
                    text=segment,
                    start=start,
                    end=end,
                    ordinal=ordinal,
                )
            )

        if end >= length:
            break
        next_start = end - overlap
        # 极短句块可能与 overlap 冲突，此时放弃重叠以保证严格前进。
        start = next_start if next_start > start else end

    return chunks
