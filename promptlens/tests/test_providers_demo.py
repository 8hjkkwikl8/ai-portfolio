from __future__ import annotations

import io
import json
import os
import socket
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from promptlens.cli import run_demo
from promptlens.providers import (
    OpenAICompatibleProvider,
    ProviderConfigurationError,
    ProviderPermanentError,
    ProviderTimeout,
)


class FakeResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class OpenAIProviderTests(unittest.TestCase):
    def test_environment_is_required(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ProviderConfigurationError, "OPENAI_BASE_URL"):
                OpenAICompatibleProvider()

    def test_compatible_request_uses_urllib_and_environment(self) -> None:
        environment = {
            "OPENAI_BASE_URL": "https://example.test/v1/",
            "OPENAI_API_KEY": "secret-test",
            "OPENAI_MODEL": "test-model",
        }
        response = FakeResponse({"choices": [{"message": {"content": "咨询｜测试"}}]})
        with patch.dict(os.environ, environment, clear=True):
            provider = OpenAICompatibleProvider()
            with patch("urllib.request.urlopen", return_value=response) as urlopen:
                result = provider.generate(
                    "你好",
                    model="test-model",
                    parameters={"temperature": 0, "seed": 42},
                    timeout=1.5,
                )
        self.assertEqual(result.text, "咨询｜测试")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/v1/chat/completions")
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-test")
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 1.5)
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "test-model")
        self.assertEqual(payload["seed"], 42)

    def test_reserved_parameters_cannot_override_audited_request(self) -> None:
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model")
        for parameters in ({"model": "other"}, {"messages": []}):
            with self.subTest(parameters=parameters):
                with self.assertRaisesRegex(ProviderConfigurationError, "保留字段"):
                    provider.generate("你好", model="model", parameters=parameters, timeout=0.1)

    def test_http_400_is_permanent_error(self) -> None:
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model")
        failure = urllib.error.HTTPError(
            "https://example.test/v1/chat/completions",
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"bad request"}'),
        )
        with patch("urllib.request.urlopen", side_effect=failure):
            with self.assertRaises(ProviderPermanentError):
                provider.generate("你好", model="model", parameters={}, timeout=0.1)

    def test_socket_timeout_becomes_provider_timeout(self) -> None:
        provider = OpenAICompatibleProvider("https://example.test/v1", "key", "model")
        with patch("urllib.request.urlopen", side_effect=socket.timeout("timeout")):
            with self.assertRaises(ProviderTimeout):
                provider.generate("你好", model="model", parameters={}, timeout=0.1)


class DemoTests(unittest.TestCase):
    def test_demo_generates_pipeline_difference_and_all_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            summary = run_demo(Path(temporary) / "artifacts")
            output = Path(summary["output_dir"])
            for name in ("baseline.json", "candidate.json", "comparison.json", "report.html"):
                self.assertTrue((output / name).is_file(), name)
            self.assertLess(
                summary["baseline_metrics"]["exact_match"],
                summary["candidate_metrics"]["exact_match"],
            )
            self.assertGreater(summary["delta"]["exact_match"], 0)
            report = (output / "report.html").read_text(encoding="utf-8")
            self.assertIn("离线流水线演示", report)
            self.assertIn("未调用真实模型", report)
            self.assertIn("不构成真实模型或提示词质量提升证据", report)
            self.assertEqual(len(list((output / "registry").glob("p_*.json"))), 2)


if __name__ == "__main__":
    unittest.main()
