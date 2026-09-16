from __future__ import annotations

import asyncio
import unittest
import urllib.error
from unittest.mock import patch

from fastapi.testclient import TestClient

from damai_agent.api import create_app
from damai_agent.config import Settings


class ApiSecurityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.internal_key = "i" * 32
        settings = Settings(
            environment="production",
            provider="openai_compatible",
            llm_api_key="l" * 32,
            llm_model="model-a",
            internal_api_key=self.internal_key,
            java_tool_api_key="j" * 32,
        )
        self.client = TestClient(create_app(settings))

    def test_health_is_minimal_and_does_not_disclose_java_url(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["environment"], "production")
        self.assertNotIn("javaBaseUrl", response.json())

    def test_chat_requires_internal_key_in_production(self) -> None:
        response = self.client.post("/api/v1/chat", json={"message": "hello"})

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["detail"], "Agent API 鉴权失败")

    def test_chat_rejects_incorrect_internal_key(self) -> None:
        response = self.client.post(
            "/api/v1/chat",
            headers={"X-Agent-Internal-Key": "x" * 32},
            json={"message": "hello"},
        )

        self.assertEqual(response.status_code, 401)

    @patch(
        "damai_agent.providers.urllib.request.urlopen",
        side_effect=urllib.error.URLError("secret upstream detail"),
    )
    def test_chat_provider_error_uses_stable_public_error(self, _: object) -> None:
        response = self.client.post(
            "/api/v1/chat",
            headers={"X-Agent-Internal-Key": self.internal_key},
            json={"message": "hello"},
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["detail"]["code"], "PROVIDER_UNAVAILABLE")
        self.assertNotIn("secret upstream detail", response.text)

    @patch(
        "damai_agent.providers.urllib.request.urlopen",
        side_effect=urllib.error.URLError("secret upstream detail"),
    )
    def test_sse_provider_error_does_not_expose_raw_exception(self, _: object) -> None:
        with self.client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"X-Agent-Internal-Key": self.internal_key},
            json={"message": "hello"},
        ) as response:
            body = "".join(response.iter_text())

        self.assertEqual(response.status_code, 200)
        self.assertIn("PROVIDER_UNAVAILABLE", body)
        self.assertNotIn("secret upstream detail", body)

    def test_sse_idle_timeout_returns_stable_failure_event(self) -> None:
        class IdleRunner:
            tool_names: list[str] = []

            async def run(self, *_: object, **__: object) -> None:
                await asyncio.sleep(1)

        settings = Settings(
            environment="production",
            provider="openai_compatible",
            llm_api_key="l" * 32,
            llm_model="model-a",
            internal_api_key=self.internal_key,
            java_tool_api_key="j" * 32,
            stream_idle_timeout_seconds=0.02,
        )
        with patch("damai_agent.api.build_runner", return_value=IdleRunner()):
            client = TestClient(create_app(settings))

        with client.stream(
            "POST",
            "/api/v1/chat/stream",
            headers={"X-Agent-Internal-Key": self.internal_key},
            json={"message": "hello"},
        ) as response:
            body = "".join(response.iter_text())

        self.assertEqual(response.status_code, 200)
        self.assertIn("STREAM_IDLE_TIMEOUT", body)
        self.assertNotIn("CancelledError", body)


if __name__ == "__main__":
    unittest.main()
