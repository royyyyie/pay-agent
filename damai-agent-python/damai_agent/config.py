"""Validated, profile-aware configuration for the Agent service."""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Literal, Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Environment(str, Enum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


_ENV_FIELDS = {
    "environment": "DAMAI_AGENT_ENVIRONMENT",
    "internal_api_key": "DAMAI_AGENT_INTERNAL_API_KEY",
    "provider": "DAMAI_AGENT_PROVIDER",
    "host": "DAMAI_AGENT_HOST",
    "port": "DAMAI_AGENT_PORT",
    "java_base_url": "DAMAI_JAVA_BASE_URL",
    "java_tool_api_key": "DAMAI_JAVA_TOOL_API_KEY",
    "java_timeout_seconds": "DAMAI_JAVA_TIMEOUT_SECONDS",
    "llm_base_url": "DAMAI_LLM_BASE_URL",
    "llm_api_key": "DAMAI_LLM_API_KEY",
    "llm_model": "DAMAI_LLM_MODEL",
    "llm_timeout_seconds": "DAMAI_LLM_TIMEOUT_SECONDS",
    "llm_max_retries": "DAMAI_LLM_MAX_RETRIES",
    "llm_retry_backoff_seconds": "DAMAI_LLM_RETRY_BACKOFF_SECONDS",
    "llm_fallback_base_url": "DAMAI_LLM_FALLBACK_BASE_URL",
    "llm_fallback_api_key": "DAMAI_LLM_FALLBACK_API_KEY",
    "llm_fallback_model": "DAMAI_LLM_FALLBACK_MODEL",
    "llm_pricing": "DAMAI_LLM_PRICING_JSON",
    "max_turn_tokens": "DAMAI_AGENT_MAX_TURN_TOKENS",
    "max_turn_cost_micro_usd": "DAMAI_AGENT_MAX_TURN_COST_MICRO_USD",
    "stream_idle_timeout_seconds": "DAMAI_AGENT_STREAM_IDLE_TIMEOUT_SECONDS",
    "max_tool_rounds": "DAMAI_AGENT_MAX_TOOL_ROUNDS",
    "max_concurrent_read_tools": "DAMAI_AGENT_MAX_CONCURRENT_READ_TOOLS",
    "tool_timeout_seconds": "DAMAI_AGENT_TOOL_TIMEOUT_SECONDS",
    "max_context_chars": "DAMAI_AGENT_MAX_CONTEXT_CHARS",
    "max_tool_result_chars": "DAMAI_AGENT_MAX_TOOL_RESULT_CHARS",
    "runtime_backend": "DAMAI_AGENT_RUNTIME_BACKEND",
    "postgres_dsn": "DAMAI_AGENT_POSTGRES_DSN",
    "redis_url": "DAMAI_AGENT_REDIS_URL",
    "delegation_hmac_key": "DAMAI_AGENT_DELEGATION_HMAC_KEY",
    "event_poll_seconds": "DAMAI_AGENT_EVENT_POLL_SECONDS",
    "otlp_traces_endpoint": "DAMAI_AGENT_OTLP_TRACES_ENDPOINT",
    "tenant_daily_cost_micro_usd": "DAMAI_AGENT_TENANT_DAILY_COST_MICRO_USD",
    "persist_tool_audit": "DAMAI_AGENT_PERSIST_TOOL_AUDIT",
    "rag_enabled": "DAMAI_AGENT_RAG_ENABLED",
    "rag_backend": "DAMAI_AGENT_RAG_BACKEND",
    "knowledge_catalog_path": "DAMAI_AGENT_KNOWLEDGE_CATALOG_PATH",
    "knowledge_source_hosts": "DAMAI_AGENT_KNOWLEDGE_SOURCE_HOSTS",
    "rag_top_k": "DAMAI_AGENT_RAG_TOP_K",
    "rag_max_context_chars": "DAMAI_AGENT_RAG_MAX_CONTEXT_CHARS",
    "rag_min_score": "DAMAI_AGENT_RAG_MIN_SCORE",
    "elasticsearch_url": "DAMAI_AGENT_ELASTICSEARCH_URL",
    "elasticsearch_api_key": "DAMAI_AGENT_ELASTICSEARCH_API_KEY",
    "elasticsearch_index_alias": "DAMAI_AGENT_ELASTICSEARCH_INDEX_ALIAS",
    "knowledge_index_version": "DAMAI_AGENT_KNOWLEDGE_INDEX_VERSION",
    "elasticsearch_timeout_seconds": "DAMAI_AGENT_ELASTICSEARCH_TIMEOUT_SECONDS",
}

_WEAK_SECRETS = {"", "change-me", "change-me-local", "changeme", "secret"}


def _read_dotenv(path: Path) -> Dict[str, str]:
    """Read the small dotenv subset used by this project without mutating os.environ."""

    if not path.is_file():
        return {}
    values: Dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key:
            values[normalized_key] = value.strip().strip('"').strip("'")
    return values


def load_dotenv(path: Optional[Path] = None) -> None:
    """Backward-compatible helper for callers that still expect dotenv side effects."""

    for key, value in _read_dotenv(path or Path.cwd() / ".env").items():
        os.environ.setdefault(key, value)


class ModelPrice(BaseModel):
    """Versioned micro-USD price per million billed tokens."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    prompt_micro_usd_per_million: int = Field(ge=0, le=1000000000000)
    completion_micro_usd_per_million: int = Field(ge=0, le=1000000000000)


class Settings(BaseModel):
    """Application settings with local/test and staging/production safety profiles."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    environment: Environment = Environment.LOCAL
    internal_api_key: str = Field(default="", repr=False, max_length=4096)
    provider: Literal["demo", "openai_compatible"] = "demo"
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=9010, ge=1, le=65535)
    java_base_url: str = "http://127.0.0.1:6086"
    java_tool_api_key: str = Field(default="change-me-local", repr=False, max_length=4096)
    java_timeout_seconds: float = Field(default=5.0, gt=0, le=120)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = Field(default="", repr=False, max_length=4096)
    llm_model: str = ""
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    llm_max_retries: int = Field(default=0, ge=0, le=3)
    llm_retry_backoff_seconds: float = Field(default=0.2, ge=0, le=2)
    llm_fallback_base_url: str = ""
    llm_fallback_api_key: str = Field(default="", repr=False, max_length=4096)
    llm_fallback_model: str = ""
    llm_pricing: dict[str, ModelPrice] = Field(default_factory=dict)
    max_turn_tokens: int = Field(default=0, ge=0, le=1000000)
    max_turn_cost_micro_usd: int = Field(default=0, ge=0, le=1000000000)
    stream_idle_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_tool_rounds: int = Field(default=6, ge=1, le=20)
    max_concurrent_read_tools: int = Field(default=4, ge=1, le=12)
    tool_timeout_seconds: float = Field(default=8.0, gt=0, le=120)
    max_context_chars: int = Field(default=80000, ge=512, le=1000000)
    max_tool_result_chars: int = Field(default=16000, ge=256, le=1000000)
    runtime_backend: Literal["memory", "durable"] = "memory"
    postgres_dsn: str = Field(default="", repr=False)
    redis_url: str = Field(default="", repr=False)
    delegation_hmac_key: str = Field(default="", repr=False)
    event_poll_seconds: float = Field(default=0.5, ge=0.1, le=5.0)
    otlp_traces_endpoint: str = ""
    tenant_daily_cost_micro_usd: int = Field(default=0, ge=0, le=1000000000000)
    persist_tool_audit: bool = False
    rag_enabled: bool = False
    rag_backend: Literal["local", "elasticsearch"] = "local"
    knowledge_catalog_path: str = ""
    knowledge_source_hosts: tuple[str, ...] = ()
    rag_top_k: int = Field(default=4, ge=1, le=10)
    rag_max_context_chars: int = Field(default=8000, ge=512, le=32000)
    rag_min_score: float = Field(default=0.01, ge=0, le=1)
    elasticsearch_url: str = ""
    elasticsearch_api_key: str = Field(default="", repr=False, max_length=8192)
    elasticsearch_index_alias: str = ""
    knowledge_index_version: str = ""
    elasticsearch_timeout_seconds: float = Field(default=3.0, ge=0.1, le=30)

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("provider", "rag_backend", mode="before")
    @classmethod
    def normalize_provider(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("java_base_url", "llm_base_url")
    @classmethod
    def validate_http_url(cls, value: str) -> str:
        normalized = value.rstrip("/")
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("必须是包含主机名的 http/https URL")
        if parsed.username or parsed.password:
            raise ValueError("URL 中禁止包含用户名或密码")
        return normalized

    @field_validator("llm_fallback_base_url", "otlp_traces_endpoint", "elasticsearch_url")
    @classmethod
    def validate_optional_http_url(cls, value: str) -> str:
        return cls.validate_http_url(value) if value else ""

    @field_validator("llm_pricing", mode="before")
    @classmethod
    def parse_pricing(cls, value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @field_validator("knowledge_source_hosts", mode="before")
    @classmethod
    def parse_knowledge_hosts(cls, value: Any) -> Any:
        if isinstance(value, str):
            return tuple(item.strip().lower() for item in value.split(",") if item.strip())
        return value

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "Settings":
        fallback_fields = (
            self.llm_fallback_base_url,
            self.llm_fallback_api_key,
            self.llm_fallback_model,
        )
        if any(fallback_fields) and (
            self.provider != "openai_compatible" or not all(fallback_fields)
        ):
            raise ValueError("备用模型必须同时配置 URL、API Key 和模型名")
        if self.provider != "openai_compatible" and self.llm_max_retries:
            raise ValueError("模型重试仅适用于 openai_compatible Provider")
        if len(self.llm_pricing) > 32:
            raise ValueError("模型定价表最多包含 32 个路由")
        if any(
            re.fullmatch(r"[A-Za-z0-9._/-]{1,128}", route) is None for route in self.llm_pricing
        ):
            raise ValueError("模型定价路由格式无效")
        if self.max_turn_cost_micro_usd and not self.llm_pricing:
            raise ValueError("费用预算需要 DAMAI_LLM_PRICING_JSON")
        if self.provider != "openai_compatible" and (
            self.max_turn_tokens or self.max_turn_cost_micro_usd
        ):
            raise ValueError("模型预算仅适用于 openai_compatible Provider")
        if self.provider == "openai_compatible":
            if not self.llm_api_key:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_API_KEY")
            if not self.llm_model:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_MODEL")
            if self.max_turn_cost_micro_usd or self.tenant_daily_cost_micro_usd:
                configured_models = {self.llm_model}
                if self.llm_fallback_model:
                    configured_models.add(self.llm_fallback_model)
                if any(
                    f"openai-compatible/{model}" not in self.llm_pricing
                    for model in configured_models
                ):
                    raise ValueError("费用预算缺少主模型或备用模型的定价")
        if self.otlp_traces_endpoint and not self.otlp_traces_endpoint.endswith("/v1/traces"):
            raise ValueError("OTLP traces endpoint 必须以 /v1/traces 结尾")
        if self.tenant_daily_cost_micro_usd:
            if self.runtime_backend != "durable" or not self.llm_pricing:
                raise ValueError("租户额度需要 durable runtime 和模型定价")
        if self.persist_tool_audit and self.runtime_backend != "durable":
            raise ValueError("持久化 Tool 审计需要 durable runtime")
        if self.rag_enabled and self.rag_backend == "local" and not self.knowledge_catalog_path:
            raise ValueError("本地 RAG 必须配置 DAMAI_AGENT_KNOWLEDGE_CATALOG_PATH")
        if self.rag_enabled and not self.knowledge_source_hosts:
            raise ValueError("启用 RAG 必须配置 DAMAI_AGENT_KNOWLEDGE_SOURCE_HOSTS")
        if self.rag_enabled and self.rag_backend == "elasticsearch":
            if not all(
                (
                    self.elasticsearch_url,
                    self.elasticsearch_api_key,
                    self.elasticsearch_index_alias,
                    self.knowledge_index_version,
                )
            ):
                raise ValueError("Elasticsearch RAG 配置不完整")
            if re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,254}", self.elasticsearch_index_alias) is None:
                raise ValueError("Elasticsearch 索引别名格式无效")
            if re.fullmatch(r"[A-Za-z0-9._:@-]{1,128}", self.knowledge_index_version) is None:
                raise ValueError("知识索引版本格式无效")
        if len(self.knowledge_source_hosts) > 32 or any(
            len(host) > 253
            or any(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) is None
                for label in host.split(".")
            )
            for host in self.knowledge_source_hosts
        ):
            raise ValueError("知识来源主机白名单格式无效")

        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
            if self.otlp_traces_endpoint and urlparse(self.otlp_traces_endpoint).scheme != "https":
                raise ValueError("staging/production OTLP traces endpoint 必须使用 HTTPS")
            if (
                self.rag_enabled
                and self.rag_backend == "elasticsearch"
                and urlparse(self.elasticsearch_url).scheme != "https"
            ):
                raise ValueError("staging/production Elasticsearch 必须使用 HTTPS")
            if self.provider == "demo":
                raise ValueError("staging/production 禁止使用 demo Provider")
            self._require_strong_secret(
                self.internal_api_key,
                "DAMAI_AGENT_INTERNAL_API_KEY",
            )
            self._require_strong_secret(
                self.java_tool_api_key,
                "DAMAI_JAVA_TOOL_API_KEY",
            )
            self._require_strong_secret(self.llm_api_key, "DAMAI_LLM_API_KEY", minimum=16)
            if self.llm_fallback_api_key:
                self._require_strong_secret(
                    self.llm_fallback_api_key, "DAMAI_LLM_FALLBACK_API_KEY", minimum=16
                )
        if self.runtime_backend == "durable":
            postgres_url = urlparse(self.postgres_dsn)
            redis_url = urlparse(self.redis_url)
            if postgres_url.scheme not in {"postgres", "postgresql"} or not postgres_url.hostname:
                raise ValueError("durable runtime requires DAMAI_AGENT_POSTGRES_DSN")
            if redis_url.scheme not in {"redis", "rediss"} or not redis_url.hostname:
                raise ValueError("durable runtime requires DAMAI_AGENT_REDIS_URL")
            self._require_strong_secret(self.delegation_hmac_key, "DAMAI_AGENT_DELEGATION_HMAC_KEY")
        return self

    @staticmethod
    def _require_strong_secret(value: str, name: str, minimum: int = 32) -> None:
        if value.lower() in _WEAK_SECRETS or len(value) < minimum:
            raise ValueError(f"{name} 在 staging/production 中至少需要 {minimum} 个字符")

    @property
    def requires_internal_auth(self) -> bool:
        return self.environment in {Environment.STAGING, Environment.PRODUCTION}

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        base_dir: Optional[Path] = None,
    ) -> "Settings":
        """Load defaults < .env < .env.<profile> < process environment."""

        process_values = dict(os.environ if env is None else env)
        root = base_dir or Path.cwd()
        base_values = _read_dotenv(root / ".env")
        environment_name = (
            process_values.get(
                "DAMAI_AGENT_ENVIRONMENT",
                base_values.get("DAMAI_AGENT_ENVIRONMENT", Environment.LOCAL.value),
            )
            .strip()
            .lower()
        )
        profile_values = _read_dotenv(root / f".env.{environment_name}")
        merged = {**base_values, **profile_values, **process_values}
        payload = {
            field_name: merged[environment_variable]
            for field_name, environment_variable in _ENV_FIELDS.items()
            if environment_variable in merged
        }
        return cls.model_validate(payload)
