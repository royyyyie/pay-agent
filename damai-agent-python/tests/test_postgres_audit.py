from __future__ import annotations

import os
import unittest
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg

from damai_agent.postgres_audit import AuditConflict, PostgresAuditSink
from damai_agent.runtime.hooks import AuditRecord, ToolOutcome

_AUDIT_MIGRATION = (
    Path(__file__).resolve().parents[1] / "migrations" / "004_agent_tool_audit.sql"
).read_text(encoding="utf-8")


def make_record() -> AuditRecord:
    marker = uuid.uuid4().hex
    return AuditRecord(
        occurred_at="2026-09-19T00:00:00Z",
        turn_id=f"turn-{marker}",
        trace_id="a" * 32,
        tool_call_id="call-1",
        tool_name="search_programs",
        risk=None,
        outcome=ToolOutcome(success=True, code=0, retryable=False, error_code=None, duration_ms=7),
        tenant_id=f"tenant-{marker}",
        session_key=f"session-{marker}",
    )


class AuditCodecTest(unittest.TestCase):
    def test_audit_metadata_excludes_tenant_and_session(self) -> None:
        record = make_record()
        rendered = str(record.to_dict())
        self.assertNotIn(record.tenant_id, rendered)
        self.assertNotIn(record.session_key, rendered)


@unittest.skipUnless(os.environ.get("DAMAI_TEST_POSTGRES_DSN"), "test PostgreSQL DSN not set")
class PostgresAuditIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.dsn = os.environ["DAMAI_TEST_POSTGRES_DSN"]
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            await conn.execute(_AUDIT_MIGRATION)
        self.sink = PostgresAuditSink(self.dsn)

    async def test_append_only_idempotence_and_conflict(self) -> None:
        self.assertTrue(await self.sink.check_ready())
        record = make_record()
        await self.sink(record)
        await self.sink(record)
        with self.assertRaises(AuditConflict):
            await self.sink(replace(record, tool_name="different_tool"))
        async with await psycopg.AsyncConnection.connect(self.dsn) as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    """SELECT metadata, record_sha256 FROM agent_tool_audit
                       WHERE tenant_id = %s AND session_key = %s
                         AND turn_id = %s AND tool_call_id = %s""",
                    (record.tenant_id, record.session_key, record.turn_id, record.tool_call_id),
                )
                row = await cur.fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0]["tool"], "search_programs")
        self.assertNotIn("private-user-query", str(row[0]))
