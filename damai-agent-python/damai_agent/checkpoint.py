"""Checkpoint contract and safe, non-replaying repair of interrupted tool batches."""

from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Dict, Optional, Protocol, Tuple

from .models import AgentErrorCode, ChatMessage, ToolResult


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class CheckpointConflict(RuntimeError):
    """The checkpoint was created, updated, or cleared by another owner."""


@dataclass(frozen=True, slots=True)
class AgentCheckpoint:
    tenant_id: str
    session_key: str
    turn_id: str
    iteration: int
    assistant_message: ChatMessage
    prompt_version: str
    toolset_version: str
    policy_version: str
    model_route: str
    completed_results: Tuple[Tuple[str, ToolResult], ...] = ()
    version: int = 0
    updated_at: str = field(default_factory=_utc_now)

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.session_key or not self.turn_id:
            raise ValueError("checkpoint identity is incomplete")
        if self.iteration < 1 or self.version < 0:
            raise ValueError("checkpoint iteration or version is invalid")
        if self.assistant_message.role != "assistant" or not self.assistant_message.tool_calls:
            raise ValueError("checkpoint requires an assistant tool-call batch")
        calls = self.assistant_message.tool_calls
        ids = [call.id for call in calls]
        if (
            any(not isinstance(call.id, str) or not call.id for call in calls)
            or any(not isinstance(call.name, str) or not call.name for call in calls)
            or any(not isinstance(call.arguments, dict) for call in calls)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("checkpoint tool calls are invalid")
        completed_ids = [call_id for call_id, _ in self.completed_results]
        if (
            any(not isinstance(call_id, str) for call_id in completed_ids)
            or len(completed_ids) != len(set(completed_ids))
            or not set(completed_ids) <= set(ids)
        ):
            raise ValueError("checkpoint results do not match the tool batch")
        if any(not isinstance(result, ToolResult) for _, result in self.completed_results):
            raise ValueError("checkpoint contains an invalid tool result")

    @property
    def phase(self) -> str:
        if not self.completed_results:
            return "awaiting_tools"
        if len(self.completed_results) < len(self.assistant_message.tool_calls):
            return "tool_progress"
        return "ready_to_resume"

    @property
    def pending_tool_call_ids(self) -> Tuple[str, ...]:
        completed = {call_id for call_id, _ in self.completed_results}
        return tuple(
            call.id for call in self.assistant_message.tool_calls if call.id not in completed
        )

    def with_result(self, tool_call_id: str, result: ToolResult) -> "AgentCheckpoint":
        if tool_call_id not in self.pending_tool_call_ids:
            raise ValueError("tool result is duplicate or not in the checkpoint")
        if not isinstance(result, ToolResult):
            raise ValueError("tool result type is invalid")
        return replace(
            self,
            completed_results=(*self.completed_results, (tool_call_id, copy.deepcopy(result))),
            version=self.version + 1,
            updated_at=_utc_now(),
        )

    def repaired_messages(self) -> Tuple[ChatMessage, ...]:
        """Pair every call without re-executing unknown work or asserting success."""

        completed = dict(self.completed_results)
        messages = [copy.deepcopy(self.assistant_message)]
        for call in self.assistant_message.tool_calls:
            result = completed.get(call.id)
            if result is None:
                result = ToolResult(
                    success=False,
                    code=503,
                    message="工具执行状态未确认；未自动重试",
                    retryable=False,
                    error_code=AgentErrorCode.TOOL_EXECUTION_UNKNOWN,
                )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=result.to_model_content(),
                    name=call.name,
                    tool_call_id=call.id,
                )
            )
        return tuple(messages)


class CheckpointRepository(Protocol):
    async def create(self, checkpoint: AgentCheckpoint) -> None: ...

    async def get_active(self, tenant_id: str, session_key: str) -> Optional[AgentCheckpoint]: ...

    async def update(self, checkpoint: AgentCheckpoint, expected_version: int) -> None: ...

    async def clear(
        self, tenant_id: str, session_key: str, turn_id: str, expected_version: int
    ) -> None: ...


def validate_checkpoint_update(
    current: AgentCheckpoint | None, checkpoint: AgentCheckpoint, expected_version: int
) -> None:
    """Apply the same monotonic, single-result CAS rule in every repository."""

    if (
        current is None
        or current.turn_id != checkpoint.turn_id
        or current.version != expected_version
        or checkpoint.version != expected_version + 1
        or checkpoint.iteration != current.iteration
        or checkpoint.assistant_message != current.assistant_message
        or checkpoint.prompt_version != current.prompt_version
        or checkpoint.toolset_version != current.toolset_version
        or checkpoint.policy_version != current.policy_version
        or checkpoint.model_route != current.model_route
        or checkpoint.completed_results[:-1] != current.completed_results
        or len(checkpoint.completed_results) != len(current.completed_results) + 1
    ):
        raise CheckpointConflict("stale checkpoint update")


class InMemoryCheckpointRepository:
    """Single-process test implementation; not a durable production repository."""

    def __init__(self) -> None:
        self._active: Dict[Tuple[str, str], AgentCheckpoint] = {}
        self._lock = asyncio.Lock()

    async def create(self, checkpoint: AgentCheckpoint) -> None:
        key = (checkpoint.tenant_id, checkpoint.session_key)
        async with self._lock:
            if checkpoint.version != 0 or key in self._active:
                raise CheckpointConflict("active checkpoint already exists")
            self._active[key] = copy.deepcopy(checkpoint)

    async def get_active(self, tenant_id: str, session_key: str) -> Optional[AgentCheckpoint]:
        async with self._lock:
            checkpoint = self._active.get((tenant_id, session_key))
            return copy.deepcopy(checkpoint) if checkpoint is not None else None

    async def update(self, checkpoint: AgentCheckpoint, expected_version: int) -> None:
        key = (checkpoint.tenant_id, checkpoint.session_key)
        async with self._lock:
            current = self._active.get(key)
            validate_checkpoint_update(current, checkpoint, expected_version)
            self._active[key] = copy.deepcopy(checkpoint)

    async def clear(
        self, tenant_id: str, session_key: str, turn_id: str, expected_version: int
    ) -> None:
        key = (tenant_id, session_key)
        async with self._lock:
            current = self._active.get(key)
            if current is None or current.turn_id != turn_id or current.version != expected_version:
                raise CheckpointConflict("stale checkpoint clear")
            del self._active[key]
