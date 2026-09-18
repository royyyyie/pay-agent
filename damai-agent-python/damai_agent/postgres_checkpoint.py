"""PostgreSQL-backed active checkpoints with tenant/session uniqueness and CAS writes."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

import psycopg
from psycopg.types.json import Jsonb
from pydantic import TypeAdapter, ValidationError

from .checkpoint import AgentCheckpoint, CheckpointConflict, validate_checkpoint_update

_ADAPTER = TypeAdapter(AgentCheckpoint)
_SCHEMA_VERSION = 1


def _encode(checkpoint: AgentCheckpoint) -> Dict[str, Any]:
    # JSON round-trip rejects non-serializable Tool data before a database write.
    payload: Dict[str, Any] = json.loads(_ADAPTER.dump_json(checkpoint))
    return payload


def _decode(payload: Any) -> AgentCheckpoint:
    if not isinstance(payload, dict):
        raise ValueError("invalid checkpoint payload")
    try:
        return _ADAPTER.validate_json(json.dumps(payload), strict=True)
    except (ValidationError, ValueError, TypeError) as exc:
        raise ValueError("invalid checkpoint payload") from exc


class PostgresCheckpointRepository:
    """One active turn per tenant/session. Apply migrations before constructing it.

    Each method owns a short transaction; no connection is held across model/tool I/O.
    A pooled connection provider and runtime integration remain separate work.
    """

    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    async def create(self, checkpoint: AgentCheckpoint) -> None:
        if checkpoint.version != 0:
            raise CheckpointConflict("new checkpoint must start at version zero")
        payload = _encode(checkpoint)
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
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
                        _SCHEMA_VERSION,
                        Jsonb(payload),
                        checkpoint.updated_at,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("active checkpoint already exists")

    async def get_active(self, tenant_id: str, session_key: str) -> Optional[AgentCheckpoint]:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT turn_id, version, schema_version, payload
                       FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s""",
                    (tenant_id, session_key),
                )
                row = await cur.fetchone()
        if row is None:
            return None
        return self._read_row(row, tenant_id, session_key)

    async def update(self, checkpoint: AgentCheckpoint, expected_version: int) -> None:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT turn_id, version, schema_version, payload
                       FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s FOR UPDATE""",
                    (checkpoint.tenant_id, checkpoint.session_key),
                )
                row = await cur.fetchone()
                current = (
                    self._read_row(row, checkpoint.tenant_id, checkpoint.session_key)
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
                        Jsonb(_encode(checkpoint)),
                        checkpoint.updated_at,
                        checkpoint.tenant_id,
                        checkpoint.session_key,
                        checkpoint.turn_id,
                        expected_version,
                    ),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("stale checkpoint update")

    async def clear(
        self, tenant_id: str, session_key: str, turn_id: str, expected_version: int
    ) -> None:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """DELETE FROM agent_active_checkpoint
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id = %s AND version = %s""",
                    (tenant_id, session_key, turn_id, expected_version),
                )
                if cur.rowcount != 1:
                    raise CheckpointConflict("stale checkpoint clear")

    @staticmethod
    def _read_row(row: tuple[Any, ...], tenant_id: str, session_key: str) -> AgentCheckpoint:
        turn_id, version, schema_version, payload = row
        if schema_version != _SCHEMA_VERSION:
            raise ValueError("unsupported checkpoint schema version")
        checkpoint = _decode(payload)
        if (
            checkpoint.tenant_id != tenant_id
            or checkpoint.session_key != session_key
            or checkpoint.turn_id != turn_id
            or checkpoint.version != version
        ):
            raise ValueError("checkpoint row and payload disagree")
        return checkpoint
