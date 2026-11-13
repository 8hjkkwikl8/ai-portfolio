"""可替换的文本向量化适配器。

默认实现仍是 ``retrieval.HashedNgramVectorizer``（确定性、零依赖、可离线复现）。
本模块只新增一个可选的 OpenAI 兼容 embeddings 适配器：未显式配置地址与模型时
不会创建任何网络客户端，也不会发起请求。
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence
from urllib import error, request


class EmbeddingError(RuntimeError):
    """嵌入服务不可用、超时或返回格式非法。"""


class Vectorizer(Protocol):
    """检索器依赖的最小向量化接口。"""

    name: str

    def transform_many(self, texts: Sequence[str]) -> list[dict[int, float]]: ...


def cosine_similarity(
    left: dict[int, float], right: dict[int, float]
) -> float:
    """稀疏向量的余弦相似度；任一为空时记 0。"""
    if not left or not right:
        return 0.0
    if len(left) > len(right):
        left, right = right, left
    return sum(value * right.get(index, 0.0) for index, value in left.items())


@dataclass(frozen=True, slots=True)
class EmbeddingConfig:
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    timeout_seconds: float = 30.0
    batch_size: int = 64

    @classmethod
    def from_env(
        cls, environ: Mapping[str, str] | None = None
    ) -> "EmbeddingConfig":
        env = os.environ if environ is None else environ
        api_key = env.get("INSIGHTRAG_EMBEDDING_API_KEY", "").strip()
        if not api_key:
            api_key = env.get("OPENAI_API_KEY", "").strip()
        return cls(
            base_url=env.get("INSIGHTRAG_EMBEDDING_BASE_URL", "").strip(),
            model=env.get("INSIGHTRAG_EMBEDDING_MODEL", "").strip(),
            api_key=api_key,
        )

    @property
    def configured(self) -> bool:
        # 地址和模型缺一不可；只有 API Key 或只有地址时绝不联网。
        return bool(self.base_url and self.model)


Transport = Callable[[str, dict[str, object], dict[str, str], float], dict[str, object]]


def post_json(
    url: str, payload: dict[str, object], headers: dict[str, str], timeout: float
) -> dict[str, object]:
    """最小 HTTP JSON POST，避免为一次嵌入调用引入 SDK。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    http_request = request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with request.urlopen(http_request, timeout=timeout) as response:
            raw = response.read()
    except error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        raise EmbeddingError(f"嵌入服务返回 HTTP {exc.code}：{detail}") from exc
    except (error.URLError, TimeoutError, OSError) as exc:
        raise EmbeddingError(f"无法连接嵌入服务：{exc}") from exc
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise EmbeddingError("嵌入服务返回了非 JSON 响应") from exc
    if not isinstance(data, dict):
        raise EmbeddingError("嵌入服务响应必须是 JSON 对象")
    return data


class OpenAIEmbeddingVectorizer:
    """调用 OpenAI 兼容 ``/embeddings`` 接口的稠密向量适配器。

    ``transport`` 仅用于测试注入确定性假服务；生产路径始终走 ``post_json``。
    """

    def __init__(
        self,
        config: EmbeddingConfig,
        transport: Transport | None = None,
    ) -> None:
        if not config.configured:
            raise ValueError("INSIGHTRAG_EMBEDDING_BASE_URL 与 INSIGHTRAG_EMBEDDING_MODEL 尚未配置")
        if config.batch_size <= 0:
            raise ValueError("嵌入批大小必须大于 0")
        self.config = config
        self.name = f"openai:{config.model}"
        self._transport = transport or post_json

    @property
    def endpoint(self) -> str:
        return self.config.base_url.rstrip("/") + "/embeddings"

    def transform_many(self, texts: Sequence[str]) -> list[dict[int, float]]:
        if not texts:
            return []
        vectors: list[dict[int, float]] = []
        size = self.config.batch_size
        for start in range(0, len(texts), size):
            batch = [str(text) for text in texts[start : start + size]]
            headers: dict[str, str] = {}
            if self.config.api_key:
                headers["Authorization"] = f"Bearer {self.config.api_key}"
            response = self._transport(
                self.endpoint,
                {"model": self.config.model, "input": batch},
                headers,
                self.config.timeout_seconds,
            )
            vectors.extend(self._parse(response, len(batch)))
        return vectors

    @staticmethod
    def _parse(
        response: dict[str, object], expected: int
    ) -> list[dict[int, float]]:
        items = response.get("data")
        if not isinstance(items, list) or len(items) != expected:
            raise EmbeddingError(
                f"嵌入响应条数与输入不一致：期望 {expected}，实际 {len(items) if isinstance(items, list) else '非数组'}"
            )
        if all(
            isinstance(item, dict) and isinstance(item.get("index"), int)
            for item in items
        ):
            items = sorted(items, key=lambda item: item["index"])  # type: ignore[index]
            if [item["index"] for item in items] != list(range(expected)):  # type: ignore[index]
                raise EmbeddingError("嵌入响应的 index 不连续")
        parsed: list[dict[int, float]] = []
        for item in items:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise EmbeddingError("嵌入响应缺少 embedding 数组")
            vector: dict[int, float] = {}
            for index, value in enumerate(embedding):
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingError("嵌入向量包含非数值元素")
                if not math.isfinite(value):
                    raise EmbeddingError("嵌入向量包含 NaN 或无穷大")
                vector[index] = float(value)
            norm = math.sqrt(sum(component * component for component in vector.values()))
            if norm:
                vector = {
                    index: component / norm for index, component in vector.items()
                }
            parsed.append(vector)
        return parsed


def build_embedding_vectorizer_from_env(
    environ: Mapping[str, str] | None = None,
) -> Vectorizer | None:
    """按环境变量构建适配器；未完整配置时返回 None，检索器回退哈希向量。"""
    config = EmbeddingConfig.from_env(environ)
    if not config.configured:
        return None
    return OpenAIEmbeddingVectorizer(config)
