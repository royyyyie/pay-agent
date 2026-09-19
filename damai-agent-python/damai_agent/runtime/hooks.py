"""Per-turn runtime hooks. Hook metadata deliberately excludes prompt and tool payloads."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Protocol, Sequence

from ..models import AgentErrorCode, ProviderUsage, ToolResult, ToolRisk

_trace_logger = logging.getLogger("damai_agent.trace")
_audit_logger = logging.getLogger("damai_agent.audit")
_hook_logger = logging.getLogger("damai_agent.hooks")


@dataclass(frozen=True, slots=True)
class ModelHookContext:
    turn_id: str
    trace_id: str
    round_number: int
    model_route: str


@dataclass(frozen=True, slots=True)
class ToolHookContext:
    turn_id: str
    trace_id: str
    tool_call_id: str
    tool_name: str
    risk: Optional[ToolRisk]
    tenant_id: str = ""
    session_key: str = ""


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    success: bool
    code: int
    retryable: bool
    error_code: Optional[AgentErrorCode]
    duration_ms: int

    @classmethod
    def from_result(cls, result: ToolResult, duration_ms: int) -> "ToolOutcome":
        return cls(
            success=result.success,
            code=result.code,
            retryable=result.retryable,
            error_code=result.error_code,
            duration_ms=duration_ms,
        )


@dataclass(frozen=True, slots=True)
class AuditRecord:
    occurred_at: str
    turn_id: str
    trace_id: str
    tool_call_id: str
    tool_name: str
    risk: Optional[ToolRisk]
    outcome: ToolOutcome
    tenant_id: str = ""
    session_key: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "occurredAt": self.occurred_at,
            "turnId": self.turn_id,
            "traceId": self.trace_id,
            "toolCallId": self.tool_call_id,
            "tool": self.tool_name,
            "risk": self.risk.value if self.risk else None,
            "success": self.outcome.success,
            "code": self.outcome.code,
            "retryable": self.outcome.retryable,
            "errorCode": self.outcome.error_code.value if self.outcome.error_code else None,
            "durationMs": self.outcome.duration_ms,
        }


AuditSink = Callable[[AuditRecord], Optional[Awaitable[None]]]


class RuntimeHook(Protocol):
    async def before_model(self, context: ModelHookContext) -> None: ...

    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None: ...

    async def before_tool(self, context: ToolHookContext) -> Optional[ToolResult]: ...

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None: ...


HookFactory = Callable[[], RuntimeHook]


class BaseRuntimeHook:
    async def before_model(self, context: ModelHookContext) -> None:
        return None

    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None:
        return None

    async def before_tool(self, context: ToolHookContext) -> Optional[ToolResult]:
        return None

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        return None


class PolicyHook(BaseRuntimeHook):
    def __init__(self, allowed_tool_names: frozenset[str]) -> None:
        self._allowed_tool_names = allowed_tool_names

    async def before_tool(self, context: ToolHookContext) -> Optional[ToolResult]:
        if context.tool_name in self._allowed_tool_names:
            return None
        return ToolResult(
            success=False,
            code=403,
            message=f"工具不在本轮允许列表: {context.tool_name}",
            retryable=False,
            error_code=AgentErrorCode.TOOL_SCOPE_DENIED,
        )


class TraceHook(BaseRuntimeHook):
    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None:
        _trace_logger.info(
            "model.completed traceId=%s turnId=%s round=%d "
            "modelRoute=%s finishReason=%s durationMs=%d",
            context.trace_id,
            context.turn_id,
            context.round_number,
            safe_log_label(context.model_route),
            safe_log_label(finish_reason),
            duration_ms,
        )

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        _trace_logger.info(
            "tool.completed traceId=%s turnId=%s toolCallId=%s tool=%s code=%d durationMs=%d",
            context.trace_id,
            context.turn_id,
            context.tool_call_id,
            safe_log_label(context.tool_name),
            outcome.code,
            outcome.duration_ms,
        )


class UsageHook(BaseRuntimeHook):
    def __init__(self) -> None:
        self.total = ProviderUsage()

    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None:
        self.total = self.total + usage


class AuditHook(BaseRuntimeHook):
    def __init__(self, sink: Optional[AuditSink] = None) -> None:
        self._sink = sink or self._log_record

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        record = AuditRecord(
            occurred_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            turn_id=context.turn_id,
            trace_id=context.trace_id,
            tool_call_id=context.tool_call_id,
            tool_name=safe_log_label(context.tool_name),
            risk=context.risk,
            outcome=outcome,
            tenant_id=context.tenant_id,
            session_key=context.session_key,
        )
        possible_awaitable = self._sink(record)
        if inspect.isawaitable(possible_awaitable):
            await possible_awaitable

    @staticmethod
    def _log_record(record: AuditRecord) -> None:
        _audit_logger.info("%s", json.dumps(record.to_dict(), separators=(",", ":")))


class HookChain:
    """Runs built-in and trusted extension hooks in order, once per turn."""

    def __init__(
        self,
        allowed_tool_names: frozenset[str],
        audit_sink: Optional[AuditSink] = None,
        extra_factories: Sequence[HookFactory] = (),
        strict_audit: bool = False,
    ) -> None:
        self.usage = UsageHook()
        self._strict_audit = strict_audit
        self._hooks: tuple[RuntimeHook, ...] = (
            PolicyHook(allowed_tool_names),
            TraceHook(),
            AuditHook(audit_sink),
            self.usage,
            *(factory() for factory in extra_factories),
        )

    async def before_model(self, context: ModelHookContext) -> None:
        for hook in self._hooks:
            await hook.before_model(context)

    async def after_model(
        self,
        context: ModelHookContext,
        usage: ProviderUsage,
        finish_reason: str,
        duration_ms: int,
    ) -> None:
        for hook in self._hooks:
            try:
                await hook.after_model(context, usage, finish_reason, duration_ms)
            except Exception:
                _hook_logger.error("after_model hook failed: %s", type(hook).__name__)

    async def before_tool(self, context: ToolHookContext) -> Optional[ToolResult]:
        for hook in self._hooks:
            try:
                decision = await hook.before_tool(context)
            except Exception:
                _hook_logger.error("before_tool hook failed: %s", type(hook).__name__)
                return ToolResult(
                    success=False,
                    code=500,
                    message="工具策略检查失败",
                    retryable=False,
                    error_code=AgentErrorCode.TOOL_EXECUTION_FAILED,
                )
            if decision is not None:
                return decision
        return None

    async def after_tool(self, context: ToolHookContext, outcome: ToolOutcome) -> None:
        for hook in self._hooks:
            try:
                await hook.after_tool(context, outcome)
            except Exception as exc:
                # Strict durable audit aborts the Turn so recovery records unknown Tool state.
                _hook_logger.error("after_tool hook failed: %s", type(hook).__name__)
                if self._strict_audit and isinstance(hook, AuditHook):
                    raise RuntimeError("tool audit persistence failed") from exc


def elapsed_ms(started_at: float) -> int:
    return max(0, int((time.perf_counter() - started_at) * 1000))


def safe_tool_call_id(value: str) -> str:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        return value
    digest = hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]
    return f"sha256:{digest}"


def safe_log_label(value: str) -> str:
    if value == "<unavailable>" or re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", value):
        return value
    return "<redacted>"
