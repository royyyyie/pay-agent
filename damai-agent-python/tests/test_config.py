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

    def test_provider_retry_and_fallback_require_complete_safe_configuration(self) -> None:
        base = {"provider": "openai_compatible", "llm_api_key": "p" * 32, "llm_model": "m"}
        with self.assertRaisesRegex(ValidationError, "模型重试仅适用于"):
            Settings(llm_max_retries=1)
        with self.assertRaises(ValidationError):
            Settings(**base, llm_max_retries=4)
        with self.assertRaisesRegex(ValidationError, "备用模型必须同时配置"):
            Settings(**base, llm_fallback_base_url="https://backup.example/v1")
        with self.assertRaisesRegex(ValidationError, "用户名或密码"):
            Settings(**base, llm_fallback_base_url="https://user:pass@backup.example/v1")
        settings = Settings(
            **base,
            llm_max_retries=1,
            llm_fallback_base_url="https://backup.example/v1",
            llm_fallback_api_key="f" * 32,
            llm_fallback_model="backup-model",
        )
        self.assertEqual(settings.llm_max_retries, 1)
        self.assertNotIn("f" * 32, repr(settings))

    def test_production_rejects_weak_fallback_key(self) -> None:
        with self.assertRaisesRegex(ValidationError, "DAMAI_LLM_FALLBACK_API_KEY"):
            Settings(
                environment="production",
                provider="openai_compatible",
                llm_api_key="p" * 32,
                llm_model="m",
                internal_api_key="i" * 32,
                java_tool_api_key="j" * 32,
                llm_fallback_base_url="https://backup.example/v1",
                llm_fallback_api_key="weak",
                llm_fallback_model="backup-model",
            )

    def test_versioned_pricing_and_turn_budget_are_validated(self) -> None:
        catalog = (
            '{"openai-compatible/model-a":{"version":"2026-09",'
            '"prompt_micro_usd_per_million":1000000,'
            '"completion_micro_usd_per_million":2000000}}'
        )
        settings = Settings.from_env(
            env={
                "DAMAI_AGENT_PROVIDER": "openai_compatible",
                "DAMAI_LLM_API_KEY": "p" * 32,
                "DAMAI_LLM_MODEL": "model-a",
                "DAMAI_LLM_PRICING_JSON": catalog,
                "DAMAI_AGENT_MAX_TURN_TOKENS": "1000",
                "DAMAI_AGENT_MAX_TURN_COST_MICRO_USD": "30000",
            }
        )
        self.assertEqual(settings.llm_pricing["openai-compatible/model-a"].version, "2026-09")
        self.assertEqual(settings.max_turn_cost_micro_usd, 30000)
        with self.assertRaisesRegex(ValidationError, "费用预算需要"):
            Settings(max_turn_cost_micro_usd=10)
        with self.assertRaisesRegex(ValidationError, "模型预算仅适用于"):
            Settings(max_turn_tokens=10)
        with self.assertRaisesRegex(ValidationError, "缺少主模型或备用模型的定价"):
            Settings(
                provider="openai_compatible",
                llm_api_key="p" * 32,
                llm_model="model-a",
                llm_pricing={
                    "openai-compatible/other": settings.llm_pricing["openai-compatible/model-a"]
                },
                max_turn_cost_micro_usd=10,
            )
        with self.assertRaises(ValidationError):
            Settings(
                llm_pricing={
                    "bad\nroute": {
                        "version": "x",
                        "prompt_micro_usd_per_million": 0,
                        "completion_micro_usd_per_million": 0,
                    }
                }
            )

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

    def test_durable_runtime_requires_its_own_connections_and_delegation_key(self) -> None:
        with self.assertRaisesRegex(ValidationError, "DAMAI_AGENT_POSTGRES_DSN"):
            Settings(runtime_backend="durable")
        with self.assertRaisesRegex(ValidationError, "DAMAI_AGENT_REDIS_URL"):
            Settings(
                runtime_backend="durable",
                postgres_dsn="postgresql://user:pass@db.example/test",
            )
        with self.assertRaisesRegex(ValidationError, "DAMAI_AGENT_DELEGATION_HMAC_KEY"):
            Settings(
                runtime_backend="durable",
                postgres_dsn="postgresql://user:pass@db.example/test",
                redis_url="rediss://cache.example:6379/0",
            )
        settings = Settings(
            runtime_backend="durable",
            postgres_dsn="postgresql://user:pass@db.example/test",
            redis_url="rediss://cache.example:6379/0",
            delegation_hmac_key="d" * 32,
        )
        self.assertNotIn("pass", repr(settings))
        self.assertNotIn("d" * 32, repr(settings))

    def test_phase3_opt_in_controls_require_safe_runtime(self) -> None:
        with self.assertRaisesRegex(ValidationError, "持久化 Tool 审计需要 durable"):
            Settings(persist_tool_audit=True)
        with self.assertRaisesRegex(ValidationError, "租户额度需要 durable"):
            Settings(tenant_daily_cost_micro_usd=100)
        with self.assertRaisesRegex(ValidationError, "/v1/traces"):
            Settings(otlp_traces_endpoint="http://collector.example:4318")
        with self.assertRaisesRegex(ValidationError, "必须使用 HTTPS"):
            Settings(
                environment="production",
                provider="openai_compatible",
                internal_api_key="i" * 32,
                java_tool_api_key="j" * 32,
                llm_api_key="l" * 32,
                llm_model="model-a",
                otlp_traces_endpoint="http://collector.example:4318/v1/traces",
            )

    def test_watch_rules_require_durable_runtime_and_strict_audit(self) -> None:
        with self.assertRaisesRegex(ValidationError, "监控规则需要 durable"):
            Settings(watch_rules_enabled=True)
        with self.assertRaisesRegex(ValidationError, "监控规则需要 durable"):
            Settings(
                runtime_backend="durable",
                postgres_dsn="postgresql://user:pass@db.example/test",
                redis_url="rediss://cache.example:6379/0",
                delegation_hmac_key="d" * 32,
                watch_rules_enabled=True,
            )
        settings = Settings(
            runtime_backend="durable",
            postgres_dsn="postgresql://user:pass@db.example/test",
            redis_url="rediss://cache.example:6379/0",
            delegation_hmac_key="d" * 32,
            persist_tool_audit=True,
            watch_rules_enabled=True,
        )
        self.assertTrue(settings.watch_rules_enabled)

    def test_rag_requires_a_bounded_catalog_configuration(self) -> None:
        with self.assertRaisesRegex(ValidationError, "KNOWLEDGE_CATALOG_PATH"):
            Settings(rag_enabled=True)
        settings = Settings.from_env(
            env={
                "DAMAI_AGENT_RAG_ENABLED": "true",
                "DAMAI_AGENT_KNOWLEDGE_CATALOG_PATH": "config/knowledge.json",
                "DAMAI_AGENT_KNOWLEDGE_SOURCE_HOSTS": "help.example.com, venue.example.com",
                "DAMAI_AGENT_RAG_TOP_K": "3",
                "DAMAI_AGENT_RAG_CANDIDATE_K": "9",
                "DAMAI_AGENT_RAG_MAX_CONTEXT_CHARS": "4096",
                "DAMAI_AGENT_RAG_MIN_SCORE": "0.05",
                "DAMAI_AGENT_RAG_HYBRID_ENABLED": "true",
                "DAMAI_AGENT_RAG_RERANK_ROLLOUT_PERCENT": "25",
                "DAMAI_AGENT_RAG_EXPERIMENT_SALT": "stable-config-salt",
            }
        )
        self.assertTrue(settings.rag_enabled)
        self.assertEqual(settings.knowledge_catalog_path, "config/knowledge.json")
        self.assertEqual(settings.knowledge_source_hosts, ("help.example.com", "venue.example.com"))
        self.assertEqual(settings.rag_top_k, 3)
        self.assertEqual(settings.rag_candidate_k, 9)
        self.assertTrue(settings.rag_hybrid_enabled)
        self.assertEqual(settings.rag_rerank_rollout_percent, 25)
        self.assertEqual(settings.rag_max_context_chars, 4096)
        self.assertEqual(settings.rag_min_score, 0.05)
        with self.assertRaisesRegex(ValidationError, "主机白名单格式无效"):
            Settings(
                rag_enabled=True,
                knowledge_catalog_path="knowledge.json",
                knowledge_source_hosts=("help..example.com",),
            )
        with self.assertRaisesRegex(ValidationError, "CANDIDATE_K"):
            Settings(rag_top_k=5, rag_candidate_k=4)
        with self.assertRaisesRegex(ValidationError, "实验盐"):
            Settings(rag_rerank_rollout_percent=10, rag_experiment_salt="too-short")

    def test_elasticsearch_rag_requires_read_only_connection_identity(self) -> None:
        with self.assertRaisesRegex(ValidationError, "配置不完整"):
            Settings(
                rag_enabled=True,
                rag_backend="elasticsearch",
                knowledge_source_hosts=("help.example.com",),
            )
        settings = Settings(
            rag_enabled=True,
            rag_backend="elasticsearch",
            knowledge_source_hosts=("help.example.com",),
            elasticsearch_url="http://es.example.com:9200",
            elasticsearch_api_key="private-es-key",
            elasticsearch_index_alias="damai-knowledge-read",
            knowledge_index_version="knowledge-2026.09.19",
        )
        self.assertEqual(settings.rag_backend, "elasticsearch")
        self.assertNotIn("private-es-key", repr(settings))
        semantic = Settings(
            rag_enabled=True,
            rag_backend="elasticsearch",
            rag_retrieval_profile="semantic_rerank",
            knowledge_source_hosts=("help.example.com",),
            elasticsearch_url="https://es.example.com:9200",
            elasticsearch_api_key="private-es-key",
            elasticsearch_index_alias="damai-knowledge-read",
            knowledge_index_version="knowledge-2026.09.19",
            elasticsearch_rerank_inference_id="enterprise-reranker-v1",
        )
        self.assertEqual(semantic.rag_retrieval_profile, "semantic_rerank")
        with self.assertRaisesRegex(ValidationError, "仅支持 Elasticsearch"):
            Settings(
                rag_enabled=True,
                knowledge_catalog_path="knowledge.json",
                knowledge_source_hosts=("help.example.com",),
                rag_retrieval_profile="semantic_hybrid",
            )
        with self.assertRaisesRegex(ValidationError, "重排推理端点"):
            Settings(
                rag_enabled=True,
                rag_backend="elasticsearch",
                rag_retrieval_profile="semantic_rerank",
                knowledge_source_hosts=("help.example.com",),
                elasticsearch_url="https://es.example.com:9200",
                elasticsearch_api_key="private-es-key",
                elasticsearch_index_alias="damai-knowledge-read",
                knowledge_index_version="knowledge-2026.09.19",
            )
        with self.assertRaisesRegex(ValidationError, "必须使用 HTTPS"):
            Settings(
                environment="production",
                provider="openai_compatible",
                internal_api_key="i" * 32,
                java_tool_api_key="j" * 32,
                llm_api_key="l" * 32,
                llm_model="model-a",
                rag_enabled=True,
                rag_backend="elasticsearch",
                knowledge_source_hosts=("help.example.com",),
                elasticsearch_url="http://es.example.com:9200",
                elasticsearch_api_key="private-es-key",
                elasticsearch_index_alias="damai-knowledge-read",
                knowledge_index_version="knowledge-2026.09.19",
            )


if __name__ == "__main__":
    unittest.main()
