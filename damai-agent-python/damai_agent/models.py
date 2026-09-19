"""Small domain models shared by providers, tools and the Agent runner."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple, Type

from pydantic import BaseModel


class ToolRisk(str, Enum):
    READ_ONLY = "READ_ONLY"
    REVERSIBLE_WRITE = "REVERSIBLE_WRITE"
    ORDER_WRITE = "ORDER_WRITE"
    PROHIBITED = "PROHIBITED"


TOOL_RISK_ORDER = {
    ToolRisk.READ_ONLY: 0,
    ToolRisk.REVERSIBLE_WRITE: 1,
    ToolRisk.ORDER_WRITE: 2,
    ToolRisk.PROHIBITED: 3,
}


class AgentErrorCode(str, Enum):
    TOOL_NOT_FOUND = "TOOL_NOT_FOUND"
    TOOL_ARGUMENT_INVALID = "TOOL_ARGUMENT_INVALID"
    TOOL_SCOPE_DENIED = "TOOL_SCOPE_DENIED"
    TOOL_RISK_DENIED = "TOOL_RISK_DENIED"
    TOOL_CALL_LIMIT_EXCEEDED = "TOOL_CALL_LIMIT_EXCEEDED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_EXECUTION_FAILED = "TOOL_EXECUTION_FAILED"
    TOOL_RESULT_INVALID = "TOOL_RESULT_INVALID"
    TOOL_RESULT_TOO_LARGE = "TOOL_RESULT_TOO_LARGE"
    PROVIDER_FINISH_REJECTED = "PROVIDER_FINISH_REJECTED"
    CONTEXT_BUDGET_EXCEEDED = "CONTEXT_BUDGET_EXCEEDED"
    CONTEXT_PROTOCOL_INVALID = "CONTEXT_PROTOCOL_INVALID"
    MODEL_ACCOUNTING_UNAVAILABLE = "MODEL_ACCOUNTING_UNAVAILABLE"
    TURN_BUDGET_EXCEEDED = "TURN_BUDGET_EXCEEDED"
    TENANT_QUOTA_EXCEEDED = "TENANT_QUOTA_EXCEEDED"
    KNOWLEDGE_CITATION_INVALID = "KNOWLEDGE_CITATION_INVALID"
    DYNAMIC_FACT_TOOL_REQUIRED = "DYNAMIC_FACT_TOOL_REQUIRED"
    RECOMMENDATION_VERIFICATION_REQUIRED = "RECOMMENDATION_VERIFICATION_REQUIRED"
    TOOL_EXECUTION_UNKNOWN = "TOOL_EXECUTION_UNKNOWN"


@dataclass(frozen=True, slots=True)
class ToolCall:
    id: str
    name: str
    arguments: Dict[str, Any]


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class ProviderUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def __add__(self, other: "ProviderUsage") -> "ProviderUsage":
        return ProviderUsage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
        )

    def to_dict(self) -> Dict[str, int]:
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "cachedTokens": self.cached_tokens,
            "reasoningTokens": self.reasoning_tokens,
            "totalTokens": self.total_tokens,
        }


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: Optional[str] = None
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    finish_reason: str = "stop"
    model_route: str = ""
    refusal: Optional[str] = None


class ProviderStreamEventType(str, Enum):
    TEXT_DELTA = "text.delta"
    TOOL_CALL_DELTA = "tool_call.delta"
    USAGE = "usage"
    COMPLETED = "completed"


@dataclass(frozen=True, slots=True)
class ProviderStreamEvent:
    event_type: ProviderStreamEventType
    text_delta: str = ""
    tool_call_index: int = 0
    tool_call_id: str = ""
    tool_name: str = ""
    arguments_delta: str = ""
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    finish_reason: str = ""
    model_route: str = ""
    refusal: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ToolSpec:
    name: str
    description: str
    parameters: Dict[str, Any]
    version: str = "1.0.0"
    risk: ToolRisk = ToolRisk.READ_ONLY
    required_scope: str = ""
    timeout_ms: int = 8000
    max_calls_per_turn: int = 3
    concurrency_safe: bool = False
    exclusive: bool = False
    request_model: Optional[Type[BaseModel]] = None
    response_model: Optional[Type[BaseModel]] = None

    def __post_init__(self) -> None:
        if isinstance(self.risk, str):
            object.__setattr__(self, "risk", ToolRisk(self.risk))
        if self.timeout_ms <= 0:
            raise ValueError("tool timeout_ms must be positive")
        if self.max_calls_per_turn <= 0:
            raise ValueError("tool max_calls_per_turn must be positive")

    def to_provider_dict(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


@dataclass(frozen=True, slots=True)
class TicketTurnContext:
    tenant_id: str
    user_id: str
    session_key: str
    turn_id: str
    request_id: str
    trace_id: str
    locale: str
    channel: str
    tool_scopes: frozenset[str]
    risk_ceiling: ToolRisk
    delegation_token_id: str
    delegation_expires_at: int | None = None


@dataclass(frozen=True, slots=True)
class ToolContext:
    session_key: str
    turn_id: str
    tool_call_id: str
    trace_id: str
    tenant_id: str = "local"
    user_id: str = "anonymous"
    tool_scopes: frozenset[str] = frozenset()
    risk_ceiling: ToolRisk = ToolRisk.READ_ONLY

    @classmethod
    def from_turn(cls, turn: TicketTurnContext, tool_call_id: str) -> "ToolContext":
        return cls(
            session_key=turn.session_key,
            turn_id=turn.turn_id,
            tool_call_id=tool_call_id,
            trace_id=turn.trace_id,
            tenant_id=turn.tenant_id,
            user_id=turn.user_id,
            tool_scopes=turn.tool_scopes,
            risk_ceiling=turn.risk_ceiling,
        )


@dataclass(frozen=True, slots=True)
class ToolResult:
    success: bool
    code: int
    message: str
    data: Any = None
    retryable: bool = False
    freshness_at: Optional[str] = None
    error_code: Optional[AgentErrorCode] = None

    def to_model_content(self) -> str:
        return json.dumps(
            {
                "success": self.success,
                "code": self.code,
                "message": self.message,
                "data": self.data,
                "retryable": self.retryable,
                "freshnessAt": self.freshness_at,
                "errorCode": self.error_code.value if self.error_code else None,
            },
            ensure_ascii=False,
            default=str,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class AgentEvent:
    event_type: str
    turn_id: str
    trace_id: str
    session_key: str
    sequence: int
    payload: Mapping[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: f"event-{uuid.uuid4()}")
    occurred_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type,
            "eventId": self.event_id,
            "eventSeq": self.sequence,
            "occurredAt": self.occurred_at,
            "turnId": self.turn_id,
            "traceId": self.trace_id,
            "sessionKey": self.session_key,
            **self.payload,
        }


@dataclass(frozen=True, slots=True)
class KnowledgeCitation:
    citation_id: str
    document_id: str
    version: str
    title: str
    source: str
    effective_from: str

    def to_dict(self) -> Dict[str, str]:
        return {
            "citationId": self.citation_id,
            "documentId": self.document_id,
            "version": self.version,
            "title": self.title,
            "source": self.source,
            "effectiveFrom": self.effective_from,
        }


@dataclass(frozen=True, slots=True)
class AgentRunSpec:
    context: TicketTurnContext
    messages: Tuple[ChatMessage, ...]
    tool_specs: Tuple[ToolSpec, ...]
    system_prompt: str
    prompt_version: str
    toolset_version: str
    policy_version: str
    model_route: str
    max_tool_rounds: int = 6
    max_tool_calls: int = 12


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    session_key: str
    turn_id: str
    trace_id: str
    final_content: str
    tools_used: Tuple[str, ...]
    messages: Tuple[ChatMessage, ...] = ()
    usage: ProviderUsage = field(default_factory=ProviderUsage)
    cost: Optional[float] = None
    cost_micro_usd: Optional[int] = None
    stop_reason: str = "stop"
    error_code: Optional[AgentErrorCode] = None
    tool_events: Tuple[AgentEvent, ...] = ()
    model_route: str = ""
    had_injections: bool = False
    citations: Tuple[KnowledgeCitation, ...] = ()
    knowledge_version: str = ""
    knowledge_variant: str = ""
    knowledge_profile: str = ""

    @property
    def answer(self) -> str:
        return self.final_content

    @property
    def tool_calls(self) -> List[str]:
        return list(self.tools_used)


RunResult = AgentRunResult
