"""Append-only PostgreSQL sink for payload-free Tool audit records."""

from __future__ import annotations

import hashlib
import json

import psycopg
from psycopg.types.json import Jsonb

from .runtime.hooks import AuditRecord


class AuditConflict(RuntimeError):
    """A Tool call identity already has a different audit record."""


class PostgresAuditSink:
    def __init__(self, dsn: str) -> None:
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")
        self._dsn = dsn

    async def check_ready(self) -> bool:
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT to_regclass('agent_tool_audit')")
                row = await cur.fetchone()
        return row is not None and row[0] is not None

    async def __call__(self, record: AuditRecord) -> None:
        if not record.tenant_id or not record.session_key:
            raise ValueError("durable audit requires tenant and session identity")
        metadata = record.to_dict()
        canonical = json.dumps(metadata, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        identity = (
            record.tenant_id,
            record.session_key,
            record.turn_id,
            record.tool_call_id,
        )
        async with await psycopg.AsyncConnection.connect(self._dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """INSERT INTO agent_tool_audit
                       (tenant_id, session_key, turn_id, tool_call_id, trace_id,
                        occurred_at, record_sha256, metadata)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT DO NOTHING RETURNING record_sha256""",
                    (*identity, record.trace_id, record.occurred_at, digest, Jsonb(metadata)),
                )
                inserted = await cur.fetchone()
                if inserted is None:
                    await cur.execute(
                        """SELECT record_sha256 FROM agent_tool_audit
                           WHERE tenant_id = %s AND session_key = %s
                             AND turn_id = %s AND tool_call_id = %s""",
                        identity,
                    )
                    old = await cur.fetchone()
                    if old is None or old[0] != digest:
                        raise AuditConflict("audit identity has conflicting metadata")
