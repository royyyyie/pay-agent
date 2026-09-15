"""The core model -> tool -> observation Agent execution loop."""

from __future__ import annotations

import inspect
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .models import (
    ChatMessage,
    ProviderResponse,
    RunResult,
    ToolContext,
)
from .providers import ModelProvider
from .session import InMemorySessionStore
from .tools import ToolRegistry

EventSink = Callable[[Dict[str, Any]], Optional[Awaitable[None]]]


SYSTEM_PROMPT = """你是面向演出购票场景的智能助手。
节目、价格、场次、规则和余量必须来自工具，禁止编造业务事实。
用户未给出节目 ID 时先搜索；存在歧义时列出候选项让用户选择。
余票是时效数据，回答时说明它只代表查询时刻。
当前版本只允许查询，不得声称已经下单、锁座、支付或绕过排队与验证码。
工具失败时如实说明，并根据 retryable 字段判断是否建议稍后重试。
回答简洁清楚，涉及金额、日期和规则时保留工具返回的原值。"""


class AgentRunner:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        sessions: InMemorySessionStore,
        max_tool_rounds: int = 6,
        tool_timeout_seconds: float = 8.0,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._sessions = sessions
        self._max_tool_rounds = max_tool_rounds
        self._tool_timeout_seconds = tool_timeout_seconds

    @property
    def tool_names(self) -> List[str]:
        return self._registry.names

    async def run(
        self,
        user_text: str,
        session_key: Optional[str] = None,
        event_sink: Optional[EventSink] = None,
    ) -> RunResult:
        normalized_session_key = session_key or f"session-{uuid.uuid4()}"
        async with self._sessions.turn_lock(normalized_session_key):
            return await self._run_locked(user_text, normalized_session_key, event_sink)

    async def _run_locked(
        self,
        user_text: str,
        session_key: str,
        event_sink: Optional[EventSink],
    ) -> RunResult:
        turn_id = f"turn-{uuid.uuid4()}"
        trace_id = uuid.uuid4().hex
        history = await self._sessions.get(session_key)
        turn_messages: List[ChatMessage] = [ChatMessage(role="user", content=user_text)]
        messages = [ChatMessage(role="system", content=SYSTEM_PROMPT), *history, *turn_messages]
        executed_tools: List[str] = []
        await self._emit(
            event_sink,
            {"type": "turn.started", "turnId": turn_id, "sessionKey": session_key},
        )

        for round_number in range(1, self._max_tool_rounds + 1):
            response = await self._provider.complete(messages, self._registry.specs)
            assistant = self._assistant_message(response)
            messages.append(assistant)
            turn_messages.append(assistant)
            await self._emit(
                event_sink,
                {
                    "type": "model.completed",
                    "turnId": turn_id,
                    "round": round_number,
                    "toolCalls": [call.name for call in response.tool_calls],
                },
            )

            if not response.tool_calls:
                answer = (response.content or "暂时无法生成回答，请稍后重试。").strip()
                await self._sessions.append(session_key, turn_messages)
                run_result = RunResult(
                    session_key=session_key,
                    turn_id=turn_id,
                    answer=answer,
                    tool_calls=executed_tools,
                )
                await self._emit(
                    event_sink,
                    {
                        "type": "turn.completed",
                        "turnId": turn_id,
                        "sessionKey": session_key,
                        "answer": answer,
                        "toolCalls": executed_tools,
                    },
                )
                return run_result

            for call in response.tool_calls:
                executed_tools.append(call.name)
                await self._emit(
                    event_sink,
                    {
                        "type": "tool.started",
                        "turnId": turn_id,
                        "toolCallId": call.id,
                        "tool": call.name,
                    },
                )
                context = ToolContext(
                    session_key=session_key,
                    turn_id=turn_id,
                    tool_call_id=call.id,
                    trace_id=trace_id,
                )
                tool_result = await self._registry.execute(
                    call.name,
                    call.arguments,
                    context,
                    timeout_seconds=self._tool_timeout_seconds,
                )
                tool_message = ChatMessage(
                    role="tool",
                    content=tool_result.to_model_content(),
                    name=call.name,
                    tool_call_id=call.id,
                )
                messages.append(tool_message)
                turn_messages.append(tool_message)
                await self._emit(
                    event_sink,
                    {
                        "type": "tool.completed",
                        "turnId": turn_id,
                        "toolCallId": call.id,
                        "tool": call.name,
                        "success": tool_result.success,
                        "code": tool_result.code,
                        "retryable": tool_result.retryable,
                    },
                )

        answer = "本轮调用工具次数已达到上限，请缩小查询范围后重试。"
        final_message = ChatMessage(role="assistant", content=answer)
        turn_messages.append(final_message)
        await self._sessions.append(session_key, turn_messages)
        await self._emit(
            event_sink,
            {
                "type": "turn.completed",
                "turnId": turn_id,
                "sessionKey": session_key,
                "answer": answer,
                "toolCalls": executed_tools,
            },
        )
        return RunResult(session_key, turn_id, answer, executed_tools)

    def _assistant_message(self, response: ProviderResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )

    async def _emit(self, sink: Optional[EventSink], event: Dict[str, Any]) -> None:
        if sink is None:
            return
        possible_awaitable = sink(event)
        if inspect.isawaitable(possible_awaitable):
            await possible_awaitable
