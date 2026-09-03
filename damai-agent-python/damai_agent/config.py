"""Environment based configuration for the Agent service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


def load_dotenv(path: Optional[Path] = None) -> None:
    """Load a small, dependency-free subset of a .env file.

    Existing environment variables always win, which keeps container and CI
    configuration predictable.
    """

    env_path = path or Path.cwd() / ".env"
    if not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _integer(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def _floating(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value else default


@dataclass(frozen=True)
class Settings:
    provider: str = "demo"
    host: str = "127.0.0.1"
    port: int = 9010
    java_base_url: str = "http://127.0.0.1:6086"
    java_tool_api_key: str = "change-me-local"
    java_timeout_seconds: float = 5.0
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: float = 30.0
    max_tool_rounds: int = 6
    tool_timeout_seconds: float = 8.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            provider=os.getenv("DAMAI_AGENT_PROVIDER", "demo").strip().lower(),
            host=os.getenv("DAMAI_AGENT_HOST", "127.0.0.1"),
            port=_integer("DAMAI_AGENT_PORT", 9010),
            java_base_url=os.getenv(
                "DAMAI_JAVA_BASE_URL", "http://127.0.0.1:6086"
            ).rstrip("/"),
            java_tool_api_key=os.getenv(
                "DAMAI_JAVA_TOOL_API_KEY", "change-me-local"
            ),
            java_timeout_seconds=_floating("DAMAI_JAVA_TIMEOUT_SECONDS", 5.0),
            llm_base_url=os.getenv(
                "DAMAI_LLM_BASE_URL", "https://api.openai.com/v1"
            ).rstrip("/"),
            llm_api_key=os.getenv("DAMAI_LLM_API_KEY", ""),
            llm_model=os.getenv("DAMAI_LLM_MODEL", ""),
            llm_timeout_seconds=_floating("DAMAI_LLM_TIMEOUT_SECONDS", 30.0),
            max_tool_rounds=_integer("DAMAI_AGENT_MAX_TOOL_ROUNDS", 6),
            tool_timeout_seconds=_floating(
                "DAMAI_AGENT_TOOL_TIMEOUT_SECONDS", 8.0
            ),
        )

    def validate(self) -> None:
        if self.provider not in {"demo", "openai_compatible"}:
            raise ValueError(
                "DAMAI_AGENT_PROVIDER 只支持 demo 或 openai_compatible"
            )
        if self.provider == "openai_compatible":
            if not self.llm_api_key:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_API_KEY")
            if not self.llm_model:
                raise ValueError("openai_compatible 模式必须配置 DAMAI_LLM_MODEL")
        if self.max_tool_rounds < 1:
            raise ValueError("DAMAI_AGENT_MAX_TOOL_ROUNDS 必须大于 0")
