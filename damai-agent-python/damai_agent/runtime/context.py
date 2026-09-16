"""Deterministic request-size limits and tool-call protocol validation."""

from __future__ import annotations

import json
from typing import Sequence

from ..models import AgentErrorCode, ChatMessage, ToolCall, ToolResult, ToolSpec


def valid_tool_protocol(messages: Sequence[ChatMessage]) -> bool:
    """An assistant tool-call batch must be followed by its results, in order."""

    if not messages or messages[0].role != "system":
        return False
    expected: list[ToolCall] = []
    for message in messages[1:]:
        if expected:
            call = expected.pop(0)
            if (
                message.role != "tool"
                or message.tool_call_id != call.id
                or message.name != call.name
            ):
                return False
            continue
        if message.role == "tool":
            return False
        if message.role == "assistant" and message.tool_calls:
            if not valid_tool_calls(message.tool_calls):
                return False
            expected.extend(message.tool_calls)
        elif message.role not in {"user", "assistant"}:
            return False
    return not expected


def valid_tool_calls(calls: Sequence[ToolCall]) -> bool:
    ids: list[str] = []
    for call in calls:
        if (
            not isinstance(call.id, str)
            or not call.id
            or not isinstance(call.name, str)
            or not call.name
            or not isinstance(call.arguments, dict)
        ):
            return False
        ids.append(call.id)
    return len(ids) == len(set(ids))


class ContextGovernor:
    """Bound the serialized model request without splitting tool-call/result batches."""

    def __init__(self, max_context_chars: int, max_tool_result_chars: int) -> None:
        if max_context_chars < 512:
            raise ValueError("max_context_chars must be at least 512")
        if max_tool_result_chars < 256:
            raise ValueError("max_tool_result_chars must be at least 256")
        self.max_context_chars = max_context_chars
        self.max_tool_result_chars = max_tool_result_chars

    def prepare(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> list[ChatMessage] | None:
        if not valid_tool_protocol(messages):
            raise ValueError("invalid tool-call protocol")
        selected = list(messages)
        while self._request_chars(selected, tools) > self.max_context_chars:
            user_positions = [
                index for index, message in enumerate(selected) if message.role == "user"
            ]
            if len(user_positions) < 2:
                return None
            # Drop one oldest complete turn; the system prompt and current turn stay intact.
            del selected[user_positions[0] : user_positions[1]]
        return selected

    def bound_tool_result(self, result: ToolResult) -> ToolResult:
        if len(result.to_model_content()) <= self.max_tool_result_chars:
            return result
        return ToolResult(
            success=False,
            code=413,
            message="工具结果过大，请缩小查询范围后重试",
            retryable=False,
            error_code=AgentErrorCode.TOOL_RESULT_TOO_LARGE,
        )

    @staticmethod
    def _request_chars(messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]) -> int:
        payload = {
            "messages": [message.to_provider_dict() for message in messages],
            "tools": [tool.to_provider_dict() for tool in tools],
        }
        return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str))
