"""Pure model-to-tool execution loop without transport or session persistence."""

from __future__ import annotations

from typing import List

from ..models import (
    AgentErrorCode,
    AgentEvent,
    AgentRunResult,
    AgentRunSpec,
    ChatMessage,
    ProviderResponse,
    ProviderStreamEventType,
    ProviderUsage,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from ..providers import ModelProvider, ProviderStreamAccumulator
from ..tools import ToolCallLedger, ToolRegistry
from .events import TurnEventEmitter

REJECTED_FINISH_REASONS = frozenset({"content_filter", "error", "refusal"})


class ToolCallingRunner:
    def __init__(
        self,
        provider: ModelProvider,
        registry: ToolRegistry,
        tool_timeout_seconds: float = 8.0,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._tool_timeout_seconds = tool_timeout_seconds

    @property
    def tool_names(self) -> List[str]:
        return self._registry.names

    @property
    def registered_specs(self) -> List[ToolSpec]:
        return self._registry.specs

    @property
    def model_route(self) -> str:
        return str(getattr(self._provider, "route_name", type(self._provider).__name__))

    def available_specs(self, spec: AgentRunSpec) -> List[ToolSpec]:
        authorized_names = {item.name for item in self._registry.available_specs(spec.context)}
        return [item for item in spec.tool_specs if item.name in authorized_names]

    async def run(
        self,
        spec: AgentRunSpec,
        emitter: TurnEventEmitter,
    ) -> AgentRunResult:
        if not spec.messages or spec.messages[-1].role != "user":
            raise ValueError("AgentRunSpec.messages 必须以当前 user 消息结束")

        available_specs = self.available_specs(spec)
        allowed_tool_names = {tool_spec.name for tool_spec in available_specs}
        messages = [ChatMessage(role="system", content=spec.system_prompt), *spec.messages]
        turn_messages: List[ChatMessage] = [spec.messages[-1]]
        tools_used: List[str] = []
        tool_events: List[AgentEvent] = []
        usage = ProviderUsage()
        model_route = spec.model_route
        ledger = ToolCallLedger(max_calls=spec.max_tool_calls)

        for round_number in range(1, spec.max_tool_rounds + 1):
            response = await self._invoke_provider(
                messages,
                available_specs,
                emitter,
                round_number,
            )
            usage = usage + response.usage
            model_route = response.model_route or model_route
            await emitter.emit(
                "model.completed",
                {
                    "round": round_number,
                    "finishReason": response.finish_reason,
                    "toolCalls": [call.name for call in response.tool_calls],
                    "usage": response.usage.to_dict(),
                    "modelRoute": model_route,
                },
            )

            if response.refusal or response.finish_reason in REJECTED_FINISH_REASONS:
                return self._rejected_result(
                    spec,
                    turn_messages,
                    tools_used,
                    tool_events,
                    usage,
                    model_route,
                    response.finish_reason,
                )

            assistant = self._assistant_message(response)
            messages.append(assistant)
            turn_messages.append(assistant)

            if not response.tool_calls:
                answer = (response.content or "暂时无法生成回答，请稍后重试。").strip()
                return AgentRunResult(
                    session_key=spec.context.session_key,
                    turn_id=spec.context.turn_id,
                    trace_id=spec.context.trace_id,
                    final_content=answer,
                    tools_used=tuple(tools_used),
                    messages=tuple(turn_messages),
                    usage=usage,
                    stop_reason=response.finish_reason,
                    tool_events=tuple(tool_events),
                    model_route=model_route,
                )

            for call in response.tool_calls:
                tools_used.append(call.name)
                started = await emitter.emit(
                    "tool.started",
                    {"toolCallId": call.id, "tool": call.name},
                )
                tool_events.append(started)
                context = ToolContext.from_turn(spec.context, call.id)
                if call.name not in allowed_tool_names:
                    tool_result = ToolResult(
                        success=False,
                        code=403,
                        message=f"工具不在本轮允许列表: {call.name}",
                        retryable=False,
                        error_code=AgentErrorCode.TOOL_SCOPE_DENIED,
                    )
                else:
                    tool_result = await self._registry.execute(
                        call.name,
                        call.arguments,
                        context,
                        timeout_seconds=self._tool_timeout_seconds,
                        ledger=ledger,
                    )
                tool_message = ChatMessage(
                    role="tool",
                    content=tool_result.to_model_content(),
                    name=call.name,
                    tool_call_id=call.id,
                )
                messages.append(tool_message)
                turn_messages.append(tool_message)
                completed = await emitter.emit(
                    "tool.completed",
                    {
                        "toolCallId": call.id,
                        "tool": call.name,
                        "success": tool_result.success,
                        "code": tool_result.code,
                        "errorCode": (
                            tool_result.error_code.value if tool_result.error_code else None
                        ),
                        "retryable": tool_result.retryable,
                    },
                )
                tool_events.append(completed)

        answer = "本轮调用工具次数已达到上限，请缩小查询范围后重试。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=usage,
            stop_reason="max_tool_rounds",
            tool_events=tuple(tool_events),
            model_route=model_route,
        )

    async def _invoke_provider(
        self,
        messages: List[ChatMessage],
        available_specs: List[ToolSpec],
        emitter: TurnEventEmitter,
        round_number: int,
    ) -> ProviderResponse:
        stream = getattr(self._provider, "stream", None)
        if not callable(stream):
            return await self._provider.complete(messages, available_specs)

        accumulator = ProviderStreamAccumulator()
        async for event in stream(messages, available_specs):
            accumulator.add(event)
            if event.event_type is ProviderStreamEventType.TEXT_DELTA and event.text_delta:
                await emitter.emit(
                    "model.text.delta",
                    {"round": round_number, "delta": event.text_delta},
                )
            elif event.event_type is ProviderStreamEventType.TOOL_CALL_DELTA:
                await emitter.emit(
                    "model.tool_call.delta",
                    {
                        "round": round_number,
                        "index": event.tool_call_index,
                        "toolCallId": event.tool_call_id,
                        "tool": event.tool_name,
                        "argumentsDelta": event.arguments_delta,
                    },
                )
            elif event.event_type is ProviderStreamEventType.USAGE:
                await emitter.emit(
                    "model.usage",
                    {"round": round_number, "usage": event.usage.to_dict()},
                )
        return accumulator.build()

    def _assistant_message(self, response: ProviderResponse) -> ChatMessage:
        return ChatMessage(
            role="assistant",
            content=response.content,
            tool_calls=response.tool_calls,
        )

    def _rejected_result(
        self,
        spec: AgentRunSpec,
        turn_messages: List[ChatMessage],
        tools_used: List[str],
        tool_events: List[AgentEvent],
        usage: ProviderUsage,
        model_route: str,
        finish_reason: str,
    ) -> AgentRunResult:
        answer = "模型未能安全完成本轮请求，请调整问题后重试。"
        turn_messages.append(ChatMessage(role="assistant", content=answer))
        return AgentRunResult(
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            trace_id=spec.context.trace_id,
            final_content=answer,
            tools_used=tuple(tools_used),
            messages=tuple(turn_messages),
            usage=usage,
            stop_reason=finish_reason or "rejected",
            error_code=AgentErrorCode.PROVIDER_FINISH_REJECTED,
            tool_events=tuple(tool_events),
            model_route=model_route,
        )
