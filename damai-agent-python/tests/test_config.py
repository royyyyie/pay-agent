from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from damai_agent.config import Environment, Settings


class SettingsTest(unittest.TestCase):
    def test_profile_layering_prefers_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / ".env").write_text(
                "DAMAI_AGENT_ENVIRONMENT=test\nDAMAI_AGENT_PORT=9001\nDAMAI_AGENT_HOST=base-host\n",
                encoding="utf-8",
            )
            (root / ".env.test").write_text(
                "DAMAI_AGENT_PORT=9002\nDAMAI_AGENT_HOST=profile-host\n",
                encoding="utf-8",
            )

            settings = Settings.from_env(
                env={"DAMAI_AGENT_PORT": "9003"},
                base_dir=root,
            )

        self.assertEqual(settings.environment, Environment.TEST)
        self.assertEqual(settings.host, "profile-host")
        self.assertEqual(settings.port, 9003)

    def test_openai_provider_requires_credentials(self) -> None:
        with self.assertRaisesRegex(ValidationError, "DAMAI_LLM_API_KEY"):
            Settings(provider="openai_compatible")

    def test_production_rejects_demo_provider(self) -> None:
        with self.assertRaisesRegex(ValidationError, "禁止使用 demo"):
            Settings(environment="production")

    def test_production_rejects_weak_internal_secret(self) -> None:
        with self.assertRaisesRegex(ValidationError, "DAMAI_AGENT_INTERNAL_API_KEY"):
            Settings(
                environment="production",
                provider="openai_compatible",
                llm_api_key="llm-key-that-is-long-enough",
                llm_model="model-a",
                internal_api_key="too-short",
                java_tool_api_key="j" * 32,
            )

    def test_production_accepts_strong_configuration(self) -> None:
        settings = Settings(
            environment="production",
            provider="openai_compatible",
            llm_api_key="l" * 32,
            llm_model="model-a",
            internal_api_key="i" * 32,
            java_tool_api_key="j" * 32,
        )

        self.assertTrue(settings.requires_internal_auth)
        self.assertNotIn("i" * 32, repr(settings))
        self.assertNotIn("j" * 32, repr(settings))

    def test_urls_reject_embedded_credentials(self) -> None:
        with self.assertRaisesRegex(ValidationError, "用户名或密码"):
            Settings(java_base_url="https://user:password@example.com")

    def test_stream_idle_timeout_loads_from_environment(self) -> None:
        settings = Settings.from_env(env={"DAMAI_AGENT_STREAM_IDLE_TIMEOUT_SECONDS": "2.5"})

        self.assertEqual(settings.stream_idle_timeout_seconds, 2.5)

    def test_concurrent_read_limit_is_configurable_and_bounded(self) -> None:
        settings = Settings.from_env(env={"DAMAI_AGENT_MAX_CONCURRENT_READ_TOOLS": "2"})

        self.assertEqual(settings.max_concurrent_read_tools, 2)
        with self.assertRaises(ValidationError):
            Settings(max_concurrent_read_tools=0)
        with self.assertRaises(ValidationError):
            Settings(max_concurrent_read_tools=13)

    def test_context_and_tool_result_character_limits_are_bounded(self) -> None:
        settings = Settings.from_env(
            env={
                "DAMAI_AGENT_MAX_CONTEXT_CHARS": "32000",
                "DAMAI_AGENT_MAX_TOOL_RESULT_CHARS": "4000",
            }
        )

        self.assertEqual(settings.max_context_chars, 32000)
        self.assertEqual(settings.max_tool_result_chars, 4000)
        with self.assertRaises(ValidationError):
            Settings(max_context_chars=511)
        with self.assertRaises(ValidationError):
            Settings(max_tool_result_chars=255)


if __name__ == "__main__":
    unittest.main()
