"""Ticket Agent lifecycle orchestration around the pure ToolCallingRunner."""

from __future__ import annotations

import hashlib
import uuid
from typing import List, Optional

from ..models import (
    AgentRunResult,
    AgentRunSpec,
    ChatMessage,
    TicketTurnContext,
    ToolRisk,
)
from ..providers import ModelProvider
from ..session import InMemorySessionStore
from ..tools import ToolRegistry
from .events import EventSink, TurnEventEmitter
from .runner import ToolCallingRunner

SYSTEM_PROMPT = """你是面向演出购票场景的智能助手。
节目、价格、场次、规则和余量必须来自工具，禁止编造业务事实。
用户未给出节目 ID 时先搜索；存在歧义时列出候选项让用户选择。
余票是时效数据，回答时说明它只代表查询时刻。
当前版本只允许查询，不得声称已经下单、锁座、支付或绕过排队与验证码。
工具失败时如实说明，并根据 retryable 字段判断是否建议稍后重试。
回答简洁清楚，涉及金额、日期和规则时保留工具返回的原值。"""


class TicketAgentLoop:
    def __init__(
        self,
        runner: ToolCallingRunner,
        sessions: InMemorySessionStore,
        max_tool_rounds: int = 6,
        max_tool_calls: int = 12,
    ) -> None:
        self._runner = runner
        self._sessions = sessions
        self._max_tool_rounds = max_tool_rounds
        self._max_tool_calls = max_tool_calls

    @property
    def tool_names(self) -> List[str]:
        return self._runner.tool_names

    async def run(
        self,
        user_text: str,
        session_key: Optional[str] = None,
        event_sink: Optional[EventSink] = None,
        *,
        trusted_context: Optional[TicketTurnContext] = None,
    ) -> AgentRunResult:
        normalized_session_key = (
            trusted_context.session_key
            if trusted_context is not None
            else session_key or f"session-{uuid.uuid4()}"
        )
        if (
            trusted_context is not None
            and session_key is not None
            and trusted_context.session_key != session_key
        ):
            raise ValueError("受信上下文的 session_key 与请求不一致")
        async with self._sessions.turn_lock(normalized_session_key):
            context = trusted_context or self._local_context(normalized_session_key)
            return await self._run_locked(user_text, context, event_sink)

    async def _run_locked(
        self,
        user_text: str,
        context: TicketTurnContext,
        event_sink: Optional[EventSink],
    ) -> AgentRunResult:
        history = await self._sessions.get(context.session_key)
        user_message = ChatMessage(role="user", content=user_text)
        tool_specs = tuple(self._runner.registered_specs)
        run_spec = AgentRunSpec(
            context=context,
            messages=tuple([*history, user_message]),
            tool_specs=tool_specs,
            system_prompt=SYSTEM_PROMPT,
            prompt_version="ticket-assistant@1",
            toolset_version=self._toolset_version(),
            policy_version="readonly-policy@1",
            model_route=self._runner.model_route,
            max_tool_rounds=self._max_tool_rounds,
            max_tool_calls=self._max_tool_calls,
        )
        emitter = TurnEventEmitter(context, event_sink)
        await emitter.emit(
            "turn.started",
            {
                "requestId": context.request_id,
                "promptVersion": run_spec.prompt_version,
                "toolsetVersion": run_spec.toolset_version,
                "policyVersion": run_spec.policy_version,
                "availableTools": [item.name for item in self._runner.available_specs(run_spec)],
            },
        )
        result = await self._runner.run(run_spec, emitter)
        await self._sessions.append(context.session_key, list(result.messages))
        await emitter.emit(
            "turn.completed",
            {
                "answer": result.final_content,
                "toolCalls": list(result.tools_used),
                "usage": result.usage.to_dict(),
                "stopReason": result.stop_reason,
                "errorCode": result.error_code.value if result.error_code else None,
                "modelRoute": result.model_route,
            },
        )
        return result

    def _local_context(self, session_key: str) -> TicketTurnContext:
        scopes = frozenset(
            spec.required_scope for spec in self._runner.registered_specs if spec.required_scope
        )
        return TicketTurnContext(
            tenant_id="local",
            user_id="anonymous",
            session_key=session_key,
            turn_id=f"turn-{uuid.uuid4()}",
            request_id=f"request-{uuid.uuid4()}",
            trace_id=uuid.uuid4().hex,
            locale="zh-CN",
            channel="direct-api",
            tool_scopes=scopes,
            risk_ceiling=ToolRisk.READ_ONLY,
            delegation_token_id="local-development",
        )

    def _toolset_version(self) -> str:
        canonical = "|".join(
            f"{spec.name}@{spec.version}"
            for spec in sorted(self._runner.registered_specs, key=lambda item: item.name)
        )
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
        return f"agent-tools-v1@sha256:{digest}"


class AgentRunner(TicketAgentLoop):
    """Compatibility facade retaining the phase 0 constructor and public API."""

    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        sessions: InMemorySessionStore,
        max_tool_rounds: int = 6,
        tool_timeout_seconds: float = 8.0,
        max_tool_calls: int = 12,
    ) -> None:
        super().__init__(
            runner=ToolCallingRunner(provider, registry, tool_timeout_seconds),
            sessions=sessions,
            max_tool_rounds=max_tool_rounds,
            max_tool_calls=max_tool_calls,
        )
