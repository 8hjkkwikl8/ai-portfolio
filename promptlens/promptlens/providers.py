"""离线确定性规则提供器与 OpenAI 兼容 HTTP 提供器。"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Mapping


class ProviderError(RuntimeError):
    """模型调用失败，可由运行器记录并有限重试。"""


class ProviderTimeout(ProviderError):
    """模型调用超时。"""


class ProviderPermanentError(ProviderError):
    """请求本身无效，重试通常不会成功。"""


class ProviderConfigurationError(ProviderError):
    """提供器配置不完整。"""


@dataclass(frozen=True, slots=True)
class GenerationResult:
    text: str
    # mock 固定为 0.0，保证离线结果可复现；网络调用由 runner 测量。
    latency_ms: float | None = None


class Provider(ABC):
    name = "abstract"
    is_mock = False

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        model: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> GenerationResult:
        raise NotImplementedError


class MockProvider(Provider):
    """可复现的离线反馈分类规则，不是真实模型，也不访问网络。"""

    name = "mock"
    is_mock = True

    _suggestion_markers = ("建议", "希望", "能不能增加", "可以支持", "最好能")
    _inquiry_markers = ("请问", "如何", "怎么", "多久", "什么时候", "哪里", "吗？", "？")
    _strong_complaints = (
        "碎了",
        "没到",
        "太慢",
        "没人解决",
        "闪退",
        "太贵",
        "损坏",
        "无法使用",
        "态度差",
    )
    _complaint_markers = ("投诉", "退款", "客服", "坏", "不满", "失望")

    _generic_reasons = {
        "咨询": "用户提出疑问",
        "投诉": "用户表达不满",
        "建议": "用户提出建议",
        "其他": "无法归入以上类别",
    }

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> GenerationResult:
        del model, parameters, timeout
        feedback = self._extract_feedback(prompt)
        improved = "先识别用户意图" in prompt and "理由要具体简洁" in prompt
        category = self._classify(feedback, improved=improved)
        reason = self._specific_reason(feedback) if improved else self._generic_reasons[category]
        if reason == "":
            reason = self._generic_reasons[category]
        return GenerationResult(text=f"{category}｜{reason}", latency_ms=0.0)

    @staticmethod
    def _extract_feedback(prompt: str) -> str:
        marker = "用户反馈：\n"
        if marker in prompt:
            return prompt.rsplit(marker, 1)[1].strip()
        return prompt.strip().splitlines()[-1].strip()

    def _classify(self, text: str, *, improved: bool) -> str:
        has_suggestion = any(marker in text for marker in self._suggestion_markers)
        has_inquiry = any(marker in text for marker in self._inquiry_markers)
        has_strong_complaint = any(marker in text for marker in self._strong_complaints)
        has_complaint = has_strong_complaint or any(marker in text for marker in self._complaint_markers)
        if improved:
            if has_suggestion:
                return "建议"
            if has_strong_complaint:
                return "投诉"
            if has_inquiry:
                return "咨询"
            if has_complaint:
                return "投诉"
            return "其他"
        # 基线模拟常见的关键词优先级与泛泛理由，而非操纵任何指标。
        if has_complaint:
            return "投诉"
        if has_inquiry:
            return "咨询"
        if has_suggestion:
            return "建议"
        return "其他"

    @staticmethod
    def _specific_reason(text: str) -> str:
        rules = (
            (("多久发货",), "询问发货时间"),
            (("屏幕碎了",), "商品到货损坏"),
            (("十天", "没到"), "物流迟迟未送达"),
            (("深色模式",), "增加深色模式"),
            (("导出Excel",), "支持导出Excel"),
            (("修改收货地址",), "询问地址修改方法"),
            (("客服", "没人解决"), "客服未及时解决问题"),
            (("发票抬头模板",), "提供发票抬头模板"),
            (("会员太贵",), "会员价格偏高"),
            (("闪退",), "应用频繁闪退"),
            (("退款流程",), "简化退款流程"),
            (("退款", "什么时候到账"), "询问退款到账时间"),
        )
        for keywords, reason in rules:
            if all(keyword in text for keyword in keywords):
                return reason
        return ""


class OpenAICompatibleProvider(Provider):
    """仅用 urllib 调用 OpenAI 兼容 /chat/completions 接口。"""

    name = "openai_compatible"
    is_mock = False

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self.base_url = (base_url or os.environ.get("OPENAI_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.default_model = default_model or os.environ.get("OPENAI_MODEL", "")
        if not self.base_url or not self.api_key or not self.default_model:
            raise ProviderConfigurationError(
                "OpenAI 兼容模式需要 OPENAI_BASE_URL、OPENAI_API_KEY、OPENAI_MODEL；"
                "离线演示请使用 provider=mock"
            )

    def generate(
        self,
        prompt: str,
        *,
        model: str,
        parameters: Mapping[str, Any],
        timeout: float,
    ) -> GenerationResult:
        selected_model = model or self.default_model
        reserved = {"model", "messages"}.intersection(parameters)
        if reserved:
            raise ProviderConfigurationError(
                f"model_params 不得覆盖保留字段：{', '.join(sorted(reserved))}"
            )
        payload = {
            **dict(parameters),
            "model": selected_model,
            "messages": [{"role": "user", "content": prompt}],
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8")
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderTimeout(f"OpenAI 兼容接口在 {timeout:g}s 后超时") from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            message = f"OpenAI 兼容接口 HTTP {exc.code}：{detail}"
            if 400 <= exc.code < 500 and exc.code not in (408, 429):
                raise ProviderPermanentError(message) from exc
            raise ProviderError(message) from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeout(f"OpenAI 兼容接口在 {timeout:g}s 后超时") from exc
            raise ProviderError(f"OpenAI 兼容接口网络错误：{exc.reason}") from exc
        except OSError as exc:
            raise ProviderError(f"OpenAI 兼容接口 I/O 错误：{exc}") from exc
        try:
            data = json.loads(body)
            text = data["choices"][0]["message"]["content"]
        except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise ProviderError("OpenAI 兼容接口响应缺少 choices[0].message.content") from exc
        if not isinstance(text, str):
            raise ProviderError("OpenAI 兼容接口返回的 message.content 不是字符串")
        return GenerationResult(text=text)
