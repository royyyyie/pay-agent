from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from damai_agent.api import create_app
from damai_agent.config import Settings
from damai_agent.delegation import DelegationError, verify_delegation
from damai_agent.models import AgentRunResult
from damai_agent.runtime.durable import SessionBusy


class DelegationTest(unittest.TestCase):
    secret = "d" * 32

    def claims(self) -> dict[str, object]:
        now = int(time.time())
        return {
            "tenantId": "tenant-1",
            "userId": "user-1",
            "sessionKey": "session-1",
            "turnId": "turn-1",
            "requestId": "request-1",
            "traceId": "trace-1",
            "locale": "zh-CN",
            "channel": "java-service",
            "toolScopes": ["programs:read"],
            "riskCeiling": "READ_ONLY",
            "delegationTokenId": "delegation-1",
            "issuedAt": now,
            "expiresAt": now + 60,
        }

    def signed_headers(self, claims: dict[str, object]) -> dict[str, str]:
        encoded = (
            base64.urlsafe_b64encode(json.dumps(claims, separators=(",", ":")).encode("utf-8"))
            .rstrip(b"=")
            .decode("ascii")
        )
        signature = hmac.new(
            self.secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256
        ).hexdigest()
        return {
            "X-Agent-Delegation": encoded,
            "X-Agent-Delegation-Signature": signature,
            "Idempotency-Key": "idem-1",
        }

    def test_signed_read_only_claims_and_tampering(self) -> None:
        headers = self.signed_headers(self.claims())
        context = verify_delegation(
            headers["X-Agent-Delegation"],
            headers["X-Agent-Delegation-Signature"],
            self.secret,
        )
        self.assertEqual(context.tenant_id, "tenant-1")
        self.assertEqual(context.tool_scopes, frozenset({"programs:read"}))
        with self.assertRaises(DelegationError):
            verify_delegation(headers["X-Agent-Delegation"], "0" * 64, self.secret)
        expired = self.claims()
        expired["expiresAt"] = int(time.time()) - 1
        headers = self.signed_headers(expired)
        with self.assertRaises(DelegationError):
            verify_delegation(
                headers["X-Agent-Delegation"],
                headers["X-Agent-Delegation-Signature"],
                self.secret,
            )
        write_claim = self.claims()
        write_claim["riskCeiling"] = "ORDER_WRITE"
        headers = self.signed_headers(write_claim)
        with self.assertRaises(DelegationError):
            verify_delegation(
                headers["X-Agent-Delegation"],
                headers["X-Agent-Delegation-Signature"],
                self.secret,
            )


class DurableApiTest(DelegationTest):
    def setUp(self) -> None:
        settings = Settings(
            runtime_backend="durable",
            postgres_dsn="postgresql://test:test@127.0.0.1:5432/test",
            redis_url="redis://127.0.0.1:6379/0",
            delegation_hmac_key=self.secret,
        )
        self.app = create_app(settings)
        self.client = TestClient(self.app)
        self.headers = self.signed_headers(self.claims())

    def test_durable_route_rejects_missing_or_mismatched_delegation(self) -> None:
        legacy = self.client.post(
            "/api/v1/chat", json={"message": "查票", "sessionKey": "session-1"}
        )
        self.assertEqual(legacy.status_code, 410)
        missing = self.client.post(
            "/api/v2/turns", json={"message": "查票", "sessionKey": "session-1"}
        )
        self.assertEqual(missing.status_code, 401)
        mismatch = self.client.post(
            "/api/v2/turns",
            headers=self.headers,
            json={"message": "查票", "sessionKey": "other"},
        )
        self.assertEqual(mismatch.status_code, 403)

    def test_durable_route_runs_with_signed_context_and_idempotency(self) -> None:
        result = AgentRunResult(
            session_key="session-1",
            turn_id="turn-1",
            trace_id="trace-1",
            final_content="已找到节目",
            tools_used=("search_programs",),
        )
        service = self.app.state.durable_service
        with patch.object(service, "run", new=AsyncMock(return_value=result)) as run:
            response = self.client.post(
                "/api/v2/turns",
                headers=self.headers,
                json={"message": "查票", "sessionKey": "session-1"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"], "已找到节目")
        self.assertEqual(run.await_args.args[1].user_id, "user-1")
        self.assertEqual(run.await_args.args[2], "idem-1")

    def test_busy_turn_is_queued_and_sse_replays_from_cursor(self) -> None:
        service = self.app.state.durable_service
        with (
            patch.object(service, "run", new=AsyncMock(side_effect=SessionBusy("busy"))),
            patch.object(service, "enqueue", new=AsyncMock(return_value=2)),
        ):
            pending = self.client.post(
                "/api/v2/turns",
                headers=self.headers,
                json={"message": "查票", "sessionKey": "session-1"},
            )
        self.assertEqual(pending.status_code, 202)
        self.assertEqual(pending.json()["position"], 2)

        turns = self.app.state.durable_turns
        event = {
            "type": "turn.completed",
            "eventSeq": 2,
            "eventId": "2",
            "turnId": "turn-1",
            "sessionKey": "session-1",
        }
        with (
            patch.object(turns, "get_turn_status", new=AsyncMock(return_value="completed")),
            patch.object(turns, "load_events_after", new=AsyncMock(return_value=(event,))) as load,
        ):
            replay = self.client.get(
                "/api/v2/turns/turn-1/events?sessionKey=session-1",
                headers={**self.headers, "Last-Event-ID": "1"},
            )
        self.assertEqual(replay.status_code, 200)
        self.assertIn("id: 2", replay.text)
        self.assertIn("event: turn.completed", replay.text)
        self.assertEqual(load.await_args.args[-1], 1)
