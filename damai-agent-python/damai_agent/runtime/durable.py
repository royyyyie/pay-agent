"""Opt-in durable Turn orchestration; deliberately separate from the default API."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
from contextlib import suppress
from typing import Optional, Sequence

from ..checkpoint import AgentCheckpoint
from ..models import (
    AgentErrorCode,
    AgentRunResult,
    AgentRunSpec,
    ChatMessage,
    TicketTurnContext,
    ToolResult,
    ToolRisk,
)
from ..postgres_turn import PostgresTurnRepository, TurnClaim, TurnConflict
from ..redis_control import RedisTurnCancellationStore
from ..redis_lease import RedisSessionLeaseStore, SessionLease
from ..redis_queue import RedisPendingTurnQueue
from .events import EventSink, TurnEventEmitter
from .loop import SYSTEM_PROMPT, toolset_version
from .runner import ToolCallingRunner


class LeaseLost(RuntimeError):
    """The Redis lease expired, changed owner, or could not be renewed."""


class TurnCancelled(RuntimeError):
    """The active Turn was cancelled at a safe point."""


class SessionBusy(TurnConflict):
    """Another lease holder or an earlier pending request owns this Session."""


class DurableTurnRecorder:
    """Writes each tool result before allowing the Runner to advance."""

    def __init__(
        self,
        turns: PostgresTurnRepository,
        leases: RedisSessionLeaseStore,
        lease: SessionLease,
        claim: TurnClaim,
        cancellations: RedisTurnCancellationStore | None = None,
    ) -> None:
        self._turns = turns
        self._leases = leases
        self._lease = lease
        self._claim = claim
        self._cancellations = cancellations
        self._checkpoint: AgentCheckpoint | None = None
        self._result_lock = asyncio.Lock()

    async def ensure_active(self) -> None:
        if not await self._leases.renew(self._lease):
            raise LeaseLost("session lease is no longer owned")
        if self._cancellations is not None and await self._cancellations.is_requested(
            self._claim.tenant_id, self._claim.session_key, self._claim.turn_id
        ):
            raise TurnCancelled("turn was cancelled at a safe point")

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

    async def finish(
        self, result: AgentRunResult, final_event: dict[str, object]
    ) -> dict[str, object]:
        await self.ensure_active()
        if self._checkpoint is not None:
            raise TurnConflict("cannot complete a turn with an active tool checkpoint")
        recorded = await self._turns.complete_turn(
            self._claim, result, result.messages, final_event=final_event
        )
        if recorded is None:
            raise RuntimeError("completed turn event was not persisted")
        return recorded


class DurableTurnService:
    """Opt-in entry point with fail-closed Turn execution and terminal recovery."""

    def __init__(
        self,
        runner: ToolCallingRunner,
        turns: PostgresTurnRepository,
        leases: RedisSessionLeaseStore,
        *,
        cancellations: RedisTurnCancellationStore | None = None,
        pending_queue: RedisPendingTurnQueue | None = None,
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
        self._cancellations = cancellations
        self._pending_queue = pending_queue
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
        pending_token = RedisPendingTurnQueue.token_for(idempotency_key, fingerprint)
        if self._pending_queue is not None:
            head = await self._pending_queue.head(context.tenant_id, context.session_key)
            if head is not None and head != pending_token:
                raise SessionBusy("session has an earlier pending request")
        lease = await self._leases.acquire(
            context.tenant_id, context.session_key, ttl_ms=self._lease_ttl_ms
        )
        if lease is None:
            raise SessionBusy("session lease is already held")
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("durable turn requires an asyncio task")
        heartbeat_failures: list[BaseException] = []
        heartbeat = asyncio.create_task(self._monitor_lease(lease, owner, heartbeat_failures))
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
                await self._acknowledge_pending(context, pending_token)
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
            recorder = DurableTurnRecorder(
                self._turns, self._leases, lease, claim, self._cancellations
            )
            await recorder.ensure_active()

            async def persist_and_forward(event: dict[str, object]) -> None:
                recorded = await self._turns.append_event(claim, event)
                await self._forward_event(event_sink, recorded)

            emitter = TurnEventEmitter(context, persist_and_forward)
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
            completed = await recorder.finish(
                result,
                {
                    "type": "turn.completed",
                    "traceId": context.trace_id,
                    "answer": result.final_content,
                    "toolCalls": list(result.tools_used),
                    "usage": result.usage.to_dict(),
                    "stopReason": result.stop_reason,
                    "errorCode": result.error_code.value if result.error_code else None,
                    "modelRoute": result.model_route,
                },
            )
            await self._acknowledge_pending(context, pending_token)
            await self._forward_event(event_sink, completed)
            return result
        except asyncio.CancelledError as exc:
            if heartbeat_failures:
                raise LeaseLost("session lease renewal failed") from heartbeat_failures[0]
            raise exc
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
            await self._leases.release(lease)

    async def cancel(
        self, user_text: str, context: TicketTurnContext, idempotency_key: str
    ) -> bool:
        """Signal the exact active request; the Runner stops at its next safe point."""

        if self._cancellations is None:
            raise ValueError("cancellation store is not configured")
        if not user_text:
            raise ValueError("user text is required")
        try:
            active = await self._turns.get_recoverable_turn(
                context.tenant_id,
                context.session_key,
                idempotency_key,
                self._request_fingerprint(user_text, context),
            )
        except TurnConflict:
            return False
        if active.turn_id != context.turn_id:
            return False
        await self._cancellations.request(context.tenant_id, context.session_key, active.turn_id)
        return True

    async def enqueue(
        self, user_text: str, context: TicketTurnContext, idempotency_key: str
    ) -> int:
        """Register a bounded pending admission; the caller retains and retries its payload."""

        if self._pending_queue is None:
            raise ValueError("pending queue is not configured")
        if not user_text or context.risk_ceiling is not ToolRisk.READ_ONLY:
            raise ValueError("pending request must be nonempty and read-only")
        token = RedisPendingTurnQueue.token_for(
            idempotency_key, self._request_fingerprint(user_text, context)
        )
        return await self._pending_queue.enqueue(context.tenant_id, context.session_key, token)

    async def recover(
        self,
        user_text: str,
        context: TicketTurnContext,
        idempotency_key: str,
    ) -> AgentRunResult:
        """Fence and terminalize the exact interrupted request without invoking a Tool."""

        if not user_text:
            raise ValueError("user text is required")
        if context.risk_ceiling is not ToolRisk.READ_ONLY:
            raise ValueError("durable runtime currently accepts read-only turns only")
        fingerprint = self._request_fingerprint(user_text, context)
        pending_token = RedisPendingTurnQueue.token_for(idempotency_key, fingerprint)
        lease = await self._leases.acquire(
            context.tenant_id, context.session_key, ttl_ms=self._lease_ttl_ms
        )
        if lease is None:
            raise TurnConflict("session lease is already held")
        owner = asyncio.current_task()
        if owner is None:
            raise RuntimeError("durable recovery requires an asyncio task")
        heartbeat_failures: list[BaseException] = []
        heartbeat = asyncio.create_task(self._monitor_lease(lease, owner, heartbeat_failures))
        try:
            candidate = await self._turns.get_recoverable_turn(
                context.tenant_id, context.session_key, idempotency_key, fingerprint
            )
            claim = await self._turns.take_over_turn(candidate)
            stored = await self._turns.load_turn_messages(claim)
            user = ChatMessage(role="user", content=user_text)
            if stored and (stored[0] != user or stored[-1].role != "tool"):
                raise TurnConflict("stored turn prefix is not recoverable")
            messages = (*stored,) if stored else (user,)
            checkpoint = await self._turns.get_checkpoint_for_turn(claim)
            model_route = self._runner.model_route
            if checkpoint is not None:
                model_route = checkpoint.model_route
                for call_id in checkpoint.pending_tool_call_ids:
                    if not await self._leases.renew(lease):
                        raise LeaseLost("session lease is no longer owned")
                    unknown = ToolResult(
                        success=False,
                        code=503,
                        message="工具执行状态未确认；未自动重试",
                        retryable=False,
                        error_code=AgentErrorCode.TOOL_EXECUTION_UNKNOWN,
                    )
                    updated = checkpoint.with_result(call_id, unknown)
                    await self._turns.update_checkpoint(claim, updated, checkpoint.version)
                    checkpoint = updated
                messages = (*messages, *checkpoint.repaired_messages())
                if not await self._leases.renew(lease):
                    raise LeaseLost("session lease is no longer owned")
                await self._turns.append_tool_round_progress(
                    claim, messages, expected_checkpoint_version=checkpoint.version
                )
            unknown_count = self._unknown_observation_count(messages)
            if not await self._leases.renew(lease):
                raise LeaseLost("session lease is no longer owned")
            answer = (
                "本轮执行已中断，部分工具状态未确认；未自动重试，请重新发起查询。"
                if unknown_count
                else "本轮执行已中断，已保存已确认的结果；请重新发起查询。"
            )
            final_messages = (*messages, ChatMessage(role="assistant", content=answer))
            tools_used = tuple(
                call.name
                for message in messages
                if message.role == "assistant"
                for call in message.tool_calls
            )
            result = AgentRunResult(
                session_key=claim.session_key,
                turn_id=claim.turn_id,
                trace_id=context.trace_id,
                final_content=answer,
                tools_used=tools_used,
                messages=final_messages,
                stop_reason="interrupted_recovered",
                error_code=(AgentErrorCode.TOOL_EXECUTION_UNKNOWN if unknown_count else None),
                model_route=model_route,
            )
            await self._turns.complete_turn(
                claim,
                result,
                final_messages,
                final_event={
                    "type": "turn.completed",
                    "traceId": context.trace_id,
                    "answer": answer,
                    "toolCalls": list(tools_used),
                    "stopReason": result.stop_reason,
                    "errorCode": result.error_code.value if result.error_code else None,
                    "modelRoute": model_route,
                    "recovered": True,
                },
            )
            await self._acknowledge_pending(context, pending_token)
            return result
        except asyncio.CancelledError as exc:
            if heartbeat_failures:
                raise LeaseLost("session lease renewal failed") from heartbeat_failures[0]
            raise exc
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
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

    @staticmethod
    def _unknown_observation_count(messages: Sequence[ChatMessage]) -> int:
        count = 0
        for message in messages:
            if message.role != "tool":
                continue
            try:
                observation = json.loads(message.content or "")
            except (TypeError, ValueError) as exc:
                raise TurnConflict("stored tool observation is invalid") from exc
            if not isinstance(observation, dict):
                raise TurnConflict("stored tool observation is invalid")
            if observation.get("errorCode") == AgentErrorCode.TOOL_EXECUTION_UNKNOWN.value:
                count += 1
        return count

    @staticmethod
    async def _forward_event(sink: Optional[EventSink], event: dict[str, object]) -> None:
        if sink is None:
            return
        delivered = sink(event)
        if inspect.isawaitable(delivered):
            await delivered

    async def _acknowledge_pending(self, context: TicketTurnContext, token: str) -> None:
        if self._pending_queue is not None:
            await self._pending_queue.acknowledge_head(
                context.tenant_id, context.session_key, token
            )

    async def _monitor_lease(
        self,
        lease: SessionLease,
        owner: asyncio.Task[object],
        failures: list[BaseException],
    ) -> None:
        while True:
            await asyncio.sleep(max(0.05, self._lease_ttl_ms / 3000))
            try:
                if not await self._leases.renew(lease):
                    raise LeaseLost("session lease is no longer owned")
            except Exception as exc:
                failures.append(exc)
                owner.cancel()
                return
