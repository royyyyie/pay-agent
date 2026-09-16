"""Validated, profile-aware configuration for the Agent service."""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Dict, Literal, Mapping, Optional
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
    "stream_idle_timeout_seconds": "DAMAI_AGENT_STREAM_IDLE_TIMEOUT_SECONDS",
    "max_tool_rounds": "DAMAI_AGENT_MAX_TOOL_ROUNDS",
    "tool_timeout_seconds": "DAMAI_AGENT_TOOL_TIMEOUT_SECONDS",
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
    stream_idle_timeout_seconds: float = Field(default=15.0, gt=0, le=120)
    max_tool_rounds: int = Field(default=6, ge=1, le=20)
    tool_timeout_seconds: float = Field(default=8.0, gt=0, le=120)

    @field_validator("environment", mode="before")
    @classmethod
    def normalize_environment(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("provider", mode="before")
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

    @model_validator(mode="after")
    def validate_cross_fields(self) -> "Settings":
        if self.provider == "openai_compatible":
            if not self.llm_api_key:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_API_KEY")
            if not self.llm_model:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_MODEL")

        if self.environment in {Environment.STAGING, Environment.PRODUCTION}:
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
