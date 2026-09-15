from __future__ import annotations

import unittest

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


if __name__ == "__main__":
    unittest.main()
