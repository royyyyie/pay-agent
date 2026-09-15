"""Small domain models shared by providers, tools and the Agent runner."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: str
    content: Optional[str] = None
    name: Optional[str] = None
    tool_call_id: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)

    def to_provider_dict(self) -> Dict[str, Any]:
        message: Dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            message["name"] = self.name
        if self.tool_call_id:
            message["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(
                            call.arguments, ensure_ascii=False, separators=(",", ":")
                        ),
                    },
                }
                for call in self.tool_calls
            ]
        return message


@dataclass(frozen=True)
class ProviderResponse:
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    version: str = "1.0.0"
    risk: str = "READ_ONLY"
    required_scope: str = ""
    timeout_ms: int = 8000

    def to_provider_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True)
class ToolContext:
    session_key: str
    turn_id: str
    tool_call_id: str
    trace_id: str


@dataclass(frozen=True)
class ToolResult:
    success: bool
    code: int
    message: str
    data: Any = None
    retryable: bool = False
    freshness_at: Optional[str] = None

    def to_model_content(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "code": self.code,
                "message": self.message,
                "data": self.data,
                "retryable": self.retryable,
                "freshnessAt": self.freshness_at,
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )


@dataclass(frozen=True)
class RunResult:
    session_key: str
    turn_id: str
    answer: str
    tool_calls: List[str]
