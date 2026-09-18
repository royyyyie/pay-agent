"""Transactional Session/Turn/message persistence with idempotency and fencing."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Sequence

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter, ValidationError

from .checkpoint import AgentCheckpoint, CheckpointConflict, validate_checkpoint_update
from .models import AgentRunResult, ChatMessage
from .postgres_checkpoint import PostgresCheckpointRepository
from .postgres_checkpoint import _encode as _checkpoint_payload

_RESULT_ADAPTER = TypeAdapter(AgentRunResult)
_MESSAGE_ADAPTER = TypeAdapter(ChatMessage)
_SCHEMA_VERSION = 1


class TurnConflict(RuntimeError):
    """A Session is busy, an idempotency key differs, or a writer lost its fence."""


@dataclass(frozen=True, slots=True)
class TurnClaim:
    tenant_id: str
    session_key: str
    turn_id: str
    state: Literal["started", "in_progress", "completed"]
    fence_version: int | None = None
    result: AgentRunResult | None = None


def _result_payload(result: AgentRunResult) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_RESULT_ADAPTER.dump_json(result))
    return payload


def _message_payload(message: ChatMessage) -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(_MESSAGE_ADAPTER.dump_json(message))
    return payload


def _read_result(payload: Any) -> AgentRunResult:
    if not isinstance(payload, dict):
        raise ValueError("invalid stored turn result")
    try:
        return _RESULT_ADAPTER.validate_json(json.dumps(payload), strict=True)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError("invalid stored turn result") from exc


def _read_message(payload: Any) -> ChatMessage:
    if not isinstance(payload, dict):
        raise ValueError("invalid stored message")
    try:
        return _MESSAGE_ADAPTER.validate_json(json.dumps(payload), strict=True)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError("invalid stored message") from exc


class PostgresTurnRepository:
    """Owns short DB transactions; callers must hold a Session lease separately.

    A fence only protects this repository's writes. It does not stop already
    dispatched external Tool calls or imply that the Agent Loop uses this store.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    async def get_active_turn(self, tenant_id: str, session_key: str) -> TurnClaim | None:
        """Read recovery metadata; only a fresh lease holder may call take_over_turn."""

        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT s.active_turn_id, s.fence_version, t.status
                       FROM agent_session s LEFT JOIN agent_turn t
                         ON t.tenant_id = s.tenant_id AND t.session_key = s.session_key
                        AND t.turn_id = s.active_turn_id
                       WHERE s.tenant_id = %s AND s.session_key = %s""",
                    (tenant_id, session_key),
                )
                row = await cur.fetchone()
        if row is None or row[0] is None:
            return None
        if row[2] != "running":
            raise ValueError("inconsistent active turn state")
        return TurnClaim(tenant_id, session_key, row[0], "in_progress", row[1])

    async def begin_turn(
        self,
        tenant_id: str,
        session_key: str,
        turn_id: str,
        idempotency_key: str,
        request_fingerprint: str,
    ) -> TurnClaim:
        if not tenant_id or not session_key or not turn_id or not idempotency_key:
            raise ValueError("incomplete turn identity")
        if len(idempotency_key) > 200 or re.fullmatch(r"[0-9a-f]{64}", request_fingerprint) is None:
            raise ValueError("invalid idempotency key or request fingerprint")
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO agent_session (tenant_id, session_key)
                       VALUES (%s, %s) ON CONFLICT (tenant_id, session_key) DO NOTHING""",
                    (tenant_id, session_key),
                )
                await cur.execute(
                    """SELECT fence_version, active_turn_id FROM agent_session
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (tenant_id, session_key),
                )
                session = await cur.fetchone()
                if session is None:
                    raise RuntimeError("session row disappeared")
                fence_version, active_turn_id = session
                await cur.execute(
                    """SELECT turn_id, request_fingerprint, status,
                              result_schema_version, result_payload
                       FROM agent_turn WHERE tenant_id = %s AND session_key = %s
                         AND idempotency_key = %s""",
                    (tenant_id, session_key, idempotency_key),
                )
                prior = await cur.fetchone()
                if prior is not None:
                    old_turn_id, old_fingerprint, status, schema_version, payload = prior
                    if old_fingerprint != request_fingerprint:
                        raise TurnConflict("idempotency key reused for a different request")
                    if status == "completed":
                        if schema_version != _SCHEMA_VERSION:
                            raise ValueError("unsupported turn result schema version")
                        result = _read_result(payload)
                        if result.turn_id != old_turn_id or result.session_key != session_key:
                            raise ValueError("turn result identity mismatch")
                        return TurnClaim(
                            tenant_id, session_key, old_turn_id, "completed", result=result
                        )
                    if status != "running" or active_turn_id != old_turn_id:
                        raise ValueError("inconsistent active turn state")
                    return TurnClaim(tenant_id, session_key, old_turn_id, "in_progress")
                if active_turn_id is not None:
                    raise TurnConflict("session already has an active turn")
                new_fence = fence_version + 1
                await cur.execute(
                    """UPDATE agent_session SET fence_version = %s, active_turn_id = %s
                       WHERE tenant_id = %s AND session_key = %s""",
                    (new_fence, turn_id, tenant_id, session_key),
                )
                await cur.execute(
                    """INSERT INTO agent_turn
                       (tenant_id, session_key, turn_id, idempotency_key,
                        request_fingerprint, fence_version, status)
                       VALUES (%s, %s, %s, %s, %s, %s, 'running')
                       ON CONFLICT DO NOTHING""",
                    (
                        tenant_id,
                        session_key,
                        turn_id,
                        idempotency_key,
                        request_fingerprint,
                        new_fence,
                    ),
                )
                if cur.rowcount != 1:
                    raise TurnConflict("turn identity already exists")
                return TurnClaim(tenant_id, session_key, turn_id, "started", new_fence)

    async def take_over_turn(self, claim: TurnClaim) -> TurnClaim:
        """Fence a crashed owner before recovery; caller must own a fresh Redis lease."""

        if claim.fence_version is None:
            raise TurnConflict("fence version is required for takeover")
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT fence_version, active_turn_id FROM agent_session
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key),
                )
                row = await cur.fetchone()
                if row is None or row != (claim.fence_version, claim.turn_id):
                    raise TurnConflict("stale turn takeover")
                await cur.execute(
                    """UPDATE agent_turn SET fence_version = %s
                       WHERE tenant_id = %s AND session_key = %s AND turn_id = %s
                         AND fence_version = %s AND status = 'running'""",
                    (
                        claim.fence_version + 1,
                        claim.tenant_id,
                        claim.session_key,
                        claim.turn_id,
                        claim.fence_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise TurnConflict("turn is no longer running")
                await cur.execute(
                    """UPDATE agent_session SET fence_version = %s
                       WHERE tenant_id = %s AND session_key = %s""",
                    (claim.fence_version + 1, claim.tenant_id, claim.session_key),
                )
                return TurnClaim(
                    claim.tenant_id,
                    claim.session_key,
                    claim.turn_id,
                    "started",
                    claim.fence_version + 1,
                )

    @staticmethod
    async def _lock_writable_claim(cur: psycopg.AsyncCursor[Any], claim: TurnClaim) -> None:
        if claim.state != "started" or claim.fence_version is None:
            raise TurnConflict("turn claim is not writable")
        await cur.execute(
            """SELECT fence_version, active_turn_id FROM agent_session
               WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
            (claim.tenant_id, claim.session_key),
        )
        session = await cur.fetchone()
        if session != (claim.fence_version, claim.turn_id):
            raise TurnConflict("stale turn checkpoint write")
        await cur.execute(
            """SELECT status, fence_version FROM agent_turn
               WHERE tenant_id = %s AND session_key = %s AND turn_id = %s FOR UPDATE""",
            (claim.tenant_id, claim.session_key, claim.turn_id),
        )
        turn = await cur.fetchone()
        if turn != ("running", claim.fence_version):
            raise TurnConflict("stale turn checkpoint write")

    async def create_checkpoint(self, claim: TurnClaim, checkpoint: AgentCheckpoint) -> None:
        if (
            checkpoint.tenant_id != claim.tenant_id
            or checkpoint.session_key != claim.session_key
            or checkpoint.turn_id != claim.turn_id
            or checkpoint.version != 0
        ):
            raise CheckpointConflict("checkpoint does not match active turn")
        payload = _checkpoint_payload(checkpoint)
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await self._lock_writable_claim(cur, claim)
                await cur.execute(
                    """INSERT INTO agent_active_checkpoint
                       (tenant_id, session_key, turn_id, version, schema_version,
                        payload, updated_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (tenant_id, session_key) DO NOTHING""",
                    (
                        checkpoint.tenant_id,
                        checkpoint.session_key,
                        checkpoint.turn_id,
                        checkpoint.version,
                        1,
                        Jsonb(payload),
                        checkpoint.updated_at,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("active checkpoint already exists")

    async def update_checkpoint(
        self, claim: TurnClaim, checkpoint: AgentCheckpoint, expected_version: int
    ) -> None:
        if (
            checkpoint.tenant_id != claim.tenant_id
            or checkpoint.session_key != claim.session_key
            or checkpoint.turn_id != claim.turn_id
        ):
            raise CheckpointConflict("checkpoint does not match active turn")
        payload = _checkpoint_payload(checkpoint)
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await self._lock_writable_claim(cur, claim)
                await cur.execute(
                    """SELECT turn_id, version, schema_version, payload
                       FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key),
                )
                row = await cur.fetchone()
                current = (
                    PostgresCheckpointRepository._read_row(row, claim.tenant_id, claim.session_key)
                    if row is not None
                    else None
                )
                validate_checkpoint_update(current, checkpoint, expected_version)
                await cur.execute(
                    """UPDATE agent_active_checkpoint
                       SET version = %s, payload = %s, updated_at = %s
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id = %s AND version = %s""",
                    (
                        checkpoint.version,
                        Jsonb(payload),
                        checkpoint.updated_at,
                        claim.tenant_id,
                        claim.session_key,
                        claim.turn_id,
                        expected_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("stale checkpoint update")

    async def clear_checkpoint(self, claim: TurnClaim, expected_version: int) -> None:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await self._lock_writable_claim(cur, claim)
                await cur.execute(
                    """DELETE FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id = %s AND version = %s""",
                    (
                        claim.tenant_id,
                        claim.session_key,
                        claim.turn_id,
                        expected_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("stale checkpoint clear")

    @staticmethod
    async def _stored_turn_messages(
        cur: psycopg.AsyncCursor[Any], claim: TurnClaim
    ) -> tuple[ChatMessage, ...]:
        await cur.execute(
            """SELECT schema_version, payload FROM agent_message
               WHERE tenant_id = %s AND session_key = %s AND turn_id = %s
               ORDER BY ordinal""",
            (claim.tenant_id, claim.session_key, claim.turn_id),
        )
        rows = await cur.fetchall()
        if any(schema_version != _SCHEMA_VERSION for schema_version, _ in rows):
            raise ValueError("unsupported stored message schema version")
        return tuple(_read_message(payload) for _, payload in rows)

    @staticmethod
    async def _append_messages(
        cur: psycopg.AsyncCursor[Any],
        claim: TurnClaim,
        messages: Sequence[ChatMessage],
        sequence: int,
        ordinal: int,
    ) -> None:
        for index, message in enumerate(messages):
            await cur.execute(
                """INSERT INTO agent_message
                   (tenant_id, session_key, sequence, turn_id, ordinal,
                    schema_version, payload)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    claim.tenant_id,
                    claim.session_key,
                    sequence + index,
                    claim.turn_id,
                    ordinal + index,
                    _SCHEMA_VERSION,
                    Jsonb(_message_payload(message)),
                ),
            )

    async def append_tool_round_progress(
        self,
        claim: TurnClaim,
        messages: Sequence[ChatMessage],
        *,
        expected_checkpoint_version: int,
    ) -> None:
        """Atomically persist a complete tool batch and retire its checkpoint."""

        if not messages or messages[0].role != "user":
            raise ValueError("tool-round progress must start with the current user message")
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await self._lock_writable_claim(cur, claim)
                await cur.execute(
                    """SELECT next_message_seq FROM agent_session
                       WHERE tenant_id = %s AND session_key = %s""",
                    (claim.tenant_id, claim.session_key),
                )
                session = await cur.fetchone()
                if session is None:
                    raise TurnConflict("session disappeared during progress commit")
                sequence = session[0]
                stored = await self._stored_turn_messages(cur, claim)
                if len(stored) >= len(messages) or tuple(messages[: len(stored)]) != stored:
                    raise TurnConflict("tool-round progress does not extend stored messages")
                await cur.execute(
                    """SELECT turn_id, version, schema_version, payload
                       FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key),
                )
                row = await cur.fetchone()
                if row is None:
                    raise CheckpointConflict("active checkpoint is missing")
                checkpoint = PostgresCheckpointRepository._read_row(
                    row, claim.tenant_id, claim.session_key
                )
                if (
                    checkpoint.turn_id != claim.turn_id
                    or checkpoint.version != expected_checkpoint_version
                    or checkpoint.phase != "ready_to_resume"
                ):
                    raise CheckpointConflict("tool batch is incomplete or stale")
                repaired = checkpoint.repaired_messages()
                if tuple(messages[-len(repaired) :]) != repaired:
                    raise TurnConflict("tool messages do not match the checkpoint")
                suffix = messages[len(stored) :]
                await self._append_messages(cur, claim, suffix, sequence, len(stored))
                await cur.execute(
                    """DELETE FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id = %s AND version = %s""",
                    (
                        claim.tenant_id,
                        claim.session_key,
                        claim.turn_id,
                        expected_checkpoint_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("checkpoint changed during progress commit")
                await cur.execute(
                    """UPDATE agent_session SET next_message_seq = %s
                       WHERE tenant_id = %s AND session_key = %s
                         AND fence_version = %s AND active_turn_id = %s""",
                    (
                        sequence + len(suffix),
                        claim.tenant_id,
                        claim.session_key,
                        claim.fence_version,
                        claim.turn_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise TurnConflict("session changed during progress commit")

    async def complete_turn(
        self,
        claim: TurnClaim,
        result: AgentRunResult,
        messages: Sequence[ChatMessage],
        *,
        expected_checkpoint_version: int | None = None,
    ) -> None:
        if claim.state != "started" or claim.fence_version is None:
            raise TurnConflict("turn claim is not writable")
        if result.turn_id != claim.turn_id or result.session_key != claim.session_key:
            raise ValueError("turn result identity mismatch")
        if not messages or (result.messages and tuple(messages) != result.messages):
            raise ValueError("committed messages do not match turn result")
        result_payload = _result_payload(result)
        # Validate serialization before any writes; existing message prefix is checked in the
        # transaction so a retry cannot silently overwrite prior tool results.
        for message in messages:
            _message_payload(message)
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT fence_version, active_turn_id, next_message_seq
                       FROM agent_session WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key),
                )
                session = await cur.fetchone()
                if (
                    session is None
                    or session[0] != claim.fence_version
                    or session[1] != claim.turn_id
                ):
                    raise TurnConflict("stale turn completion")
                await cur.execute(
                    """SELECT status, fence_version FROM agent_turn
                       WHERE tenant_id = %s AND session_key = %s AND turn_id = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key, claim.turn_id),
                )
                turn = await cur.fetchone()
                if turn != ("running", claim.fence_version):
                    raise TurnConflict("turn already completed or taken over")
                await cur.execute(
                    """SELECT turn_id, version FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (claim.tenant_id, claim.session_key),
                )
                checkpoint = await cur.fetchone()
                if expected_checkpoint_version is None:
                    if checkpoint is not None:
                        raise TurnConflict("active checkpoint must be resolved before completion")
                elif checkpoint != (claim.turn_id, expected_checkpoint_version):
                    raise TurnConflict("checkpoint version changed before completion")
                sequence = session[2]
                stored = await self._stored_turn_messages(cur, claim)
                if len(stored) > len(messages) or tuple(messages[: len(stored)]) != stored:
                    raise TurnConflict("turn result does not extend stored messages")
                suffix = messages[len(stored) :]
                await self._append_messages(cur, claim, suffix, sequence, len(stored))
                if expected_checkpoint_version is not None:
                    await cur.execute(
                        """DELETE FROM agent_active_checkpoint
                           WHERE tenant_id = %s AND session_key = %s
                             AND turn_id = %s AND version = %s""",
                        (
                            claim.tenant_id,
                            claim.session_key,
                            claim.turn_id,
                            expected_checkpoint_version,
                        ),
                    )
                    if cur.rowcount != 1:
                        raise TurnConflict("checkpoint changed during completion")
                await cur.execute(
                    """UPDATE agent_turn SET status = 'completed', result_schema_version = %s,
                          result_payload = %s, completed_at = clock_timestamp()
                       WHERE tenant_id = %s AND session_key = %s AND turn_id = %s
                         AND status = 'running' AND fence_version = %s""",
                    (
                        _SCHEMA_VERSION,
                        Jsonb(result_payload),
                        claim.tenant_id,
                        claim.session_key,
                        claim.turn_id,
                        claim.fence_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise TurnConflict("turn changed during completion")
                await cur.execute(
                    """UPDATE agent_session SET active_turn_id = NULL, next_message_seq = %s
                       WHERE tenant_id = %s AND session_key = %s
                         AND fence_version = %s AND active_turn_id = %s""",
                    (
                        sequence + len(suffix),
                        claim.tenant_id,
                        claim.session_key,
                        claim.fence_version,
                        claim.turn_id,
                    ),
                )
                if cur.rowcount != 1:
                    raise TurnConflict("session changed during completion")

    async def load_recent_messages(
        self, tenant_id: str, session_key: str, *, max_turns: int = 20
    ) -> tuple[ChatMessage, ...]:
        if not 1 <= max_turns <= 100:
            raise ValueError("max_turns must be between 1 and 100")
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """WITH recent AS (
                           SELECT turn_id FROM agent_message
                           WHERE tenant_id = %s AND session_key = %s
                           GROUP BY turn_id ORDER BY max(sequence) DESC LIMIT %s
                       )
                       SELECT schema_version, payload FROM agent_message
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id IN (SELECT turn_id FROM recent)
                       ORDER BY sequence""",
                    (tenant_id, session_key, max_turns, tenant_id, session_key),
                )
                rows = await cur.fetchall()
        for schema_version, _ in rows:
            if schema_version != _SCHEMA_VERSION:
                raise ValueError("unsupported stored message schema version")
        return tuple(_read_message(payload) for _, payload in rows)
