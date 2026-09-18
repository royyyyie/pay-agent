"""Opt-in durable Turn orchestration; deliberately separate from the default API."""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Optional, Sequence

from ..checkpoint import AgentCheckpoint
from ..models import (
    AgentRunResult,
    AgentRunSpec,
    ChatMessage,
    TicketTurnContext,
    ToolResult,
    ToolRisk,
)
from ..postgres_turn import PostgresTurnRepository, TurnClaim, TurnConflict
from ..redis_lease import RedisSessionLeaseStore, SessionLease
from .events import EventSink, TurnEventEmitter
from .loop import SYSTEM_PROMPT, toolset_version
from .runner import ToolCallingRunner


class LeaseLost(RuntimeError):
    """The Redis lease expired, changed owner, or could not be renewed."""


class DurableTurnRecorder:
    """Writes each tool result before allowing the Runner to advance."""

    def __init__(
        self,
        turns: PostgresTurnRepository,
        leases: RedisSessionLeaseStore,
        lease: SessionLease,
        claim: TurnClaim,
    ) -> None:
        self._turns = turns
        self._leases = leases
        self._lease = lease
        self._claim = claim
        self._checkpoint: AgentCheckpoint | None = None
        self._result_lock = asyncio.Lock()

    async def ensure_active(self) -> None:
        if not await self._leases.renew(self._lease):
            raise LeaseLost("session lease is no longer owned")

    async def before_tool_round(
        self, spec: AgentRunSpec, iteration: int, assistant: ChatMessage, model_route: str
    ) -> None:
        await self.ensure_active()
        if self._checkpoint is not None:
            raise TurnConflict("previous tool batch has not been committed")
        checkpoint = AgentCheckpoint(
            tenant_id=spec.context.tenant_id,
            session_key=spec.context.session_key,
            turn_id=spec.context.turn_id,
            iteration=iteration,
            assistant_message=assistant,
            prompt_version=spec.prompt_version,
            toolset_version=spec.toolset_version,
            policy_version=spec.policy_version,
            model_route=model_route,
        )
        await self._turns.create_checkpoint(self._claim, checkpoint)
        self._checkpoint = checkpoint

    async def record_tool_result(self, tool_call_id: str, result: ToolResult) -> None:
        async with self._result_lock:
            await self.ensure_active()
            checkpoint = self._checkpoint
            if checkpoint is None:
                raise TurnConflict("tool result arrived without an active checkpoint")
            updated = checkpoint.with_result(tool_call_id, result)
            await self._turns.update_checkpoint(self._claim, updated, checkpoint.version)
            self._checkpoint = updated

    async def commit_tool_round(self, messages: Sequence[ChatMessage]) -> None:
        await self.ensure_active()
        checkpoint = self._checkpoint
        if checkpoint is None or checkpoint.phase != "ready_to_resume":
            raise TurnConflict("tool batch is incomplete")
        await self._turns.append_tool_round_progress(
            self._claim, messages, expected_checkpoint_version=checkpoint.version
        )
        self._checkpoint = None

    async def finish(self, result: AgentRunResult) -> None:
        await self.ensure_active()
        if self._checkpoint is not None:
            raise TurnConflict("cannot complete a turn with an active tool checkpoint")
        await self._turns.complete_turn(self._claim, result, result.messages)


class DurableTurnService:
    """Opt-in entry point. Crashed/in-progress turns require explicit recovery later."""

    def __init__(
        self,
        runner: ToolCallingRunner,
        turns: PostgresTurnRepository,
        leases: RedisSessionLeaseStore,
        *,
        lease_ttl_ms: int = 30_000,
        max_tool_rounds: int = 6,
        max_tool_calls: int = 12,
        max_history_turns: int = 20,
    ) -> None:
        if not 100 <= lease_ttl_ms <= 300_000:
            raise ValueError("invalid session lease TTL")
        if not 1 <= max_tool_rounds <= 20 or not 1 <= max_tool_calls <= 100:
            raise ValueError("invalid tool budget")
        if not 1 <= max_history_turns <= 100:
            raise ValueError("invalid history limit")
        self._runner = runner
        self._turns = turns
        self._leases = leases
        self._lease_ttl_ms = lease_ttl_ms
        self._max_tool_rounds = max_tool_rounds
        self._max_tool_calls = max_tool_calls
        self._max_history_turns = max_history_turns

    async def run(
        self,
        user_text: str,
        context: TicketTurnContext,
        idempotency_key: str,
        event_sink: Optional[EventSink] = None,
    ) -> AgentRunResult:
        if not user_text:
            raise ValueError("user text is required")
        if context.risk_ceiling is not ToolRisk.READ_ONLY:
            raise ValueError("durable runtime currently accepts read-only turns only")
        fingerprint = self._request_fingerprint(user_text, context)
        lease = await self._leases.acquire(
            context.tenant_id, context.session_key, ttl_ms=self._lease_ttl_ms
        )
        if lease is None:
            raise TurnConflict("session lease is already held")
        try:
            claim = await self._turns.begin_turn(
                context.tenant_id,
                context.session_key,
                context.turn_id,
                idempotency_key,
                fingerprint,
            )
            if claim.state == "completed":
                if claim.result is None:
                    raise ValueError("completed turn has no result")
                return claim.result
            if claim.state != "started":
                raise TurnConflict("turn is in progress and requires explicit recovery")

            history = await self._turns.load_recent_messages(
                context.tenant_id, context.session_key, max_turns=self._max_history_turns
            )
            tool_specs = tuple(self._runner.registered_specs)
            spec = AgentRunSpec(
                context=context,
                messages=(*history, ChatMessage(role="user", content=user_text)),
                tool_specs=tool_specs,
                system_prompt=SYSTEM_PROMPT,
                prompt_version="ticket-assistant@1",
                toolset_version=toolset_version(tool_specs),
                policy_version="readonly-policy@1",
                model_route=self._runner.model_route,
                max_tool_rounds=self._max_tool_rounds,
                max_tool_calls=self._max_tool_calls,
            )
            emitter = TurnEventEmitter(context, event_sink)
            recorder = DurableTurnRecorder(self._turns, self._leases, lease, claim)
            await recorder.ensure_active()
            await emitter.emit(
                "turn.started",
                {
                    "requestId": context.request_id,
                    "promptVersion": spec.prompt_version,
                    "toolsetVersion": spec.toolset_version,
                    "policyVersion": spec.policy_version,
                    "availableTools": [item.name for item in self._runner.available_specs(spec)],
                },
            )
            result = await self._runner.run(spec, emitter, recorder=recorder)
            await recorder.finish(result)
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
        finally:
            await self._leases.release(lease)

    @staticmethod
    def _request_fingerprint(user_text: str, context: TicketTurnContext) -> str:
        canonical = json.dumps(
            {
                "userId": context.user_id,
                "message": user_text,
                "locale": context.locale,
                "channel": context.channel,
                "toolScopes": sorted(context.tool_scopes),
                "riskCeiling": context.risk_ceiling.value,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
