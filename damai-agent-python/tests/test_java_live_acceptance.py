"""Opt-in read-only acceptance against a dedicated, authenticated Java Tool Gateway."""

from __future__ import annotations

import os
import unittest
import uuid

from damai_agent.models import AgentErrorCode, ToolContext, ToolRisk
from damai_agent.tools import JavaToolClient, ToolRegistry, build_java_tools

_REQUIRED = (
    "DAMAI_TEST_JAVA_BASE_URL",
    "DAMAI_TEST_JAVA_TOOL_API_KEY",
    "DAMAI_TEST_JAVA_PROGRAM_ID",
    "DAMAI_TEST_JAVA_KEYWORD",
)


@unittest.skipUnless(
    all(os.environ.get(name) for name in _REQUIRED),
    "dedicated Java Tool acceptance environment not configured",
)
class LiveJavaToolAcceptanceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.registry = ToolRegistry(
            build_java_tools(
                JavaToolClient(
                    os.environ["DAMAI_TEST_JAVA_BASE_URL"],
                    os.environ["DAMAI_TEST_JAVA_TOOL_API_KEY"],
                    timeout_seconds=8,
                )
            )
        )
        self.context = ToolContext(
            session_key=f"live-acceptance-{uuid.uuid4().hex}",
            turn_id=f"turn-{uuid.uuid4().hex}",
            tool_call_id=f"call-{uuid.uuid4().hex}",
            trace_id=uuid.uuid4().hex,
            tenant_id="acceptance",
            user_id="acceptance",
            tool_scopes=frozenset({"programs:read"}),
            risk_ceiling=ToolRisk.READ_ONLY,
        )

    async def test_all_three_authorized_read_only_tools(self) -> None:
        program_id = int(os.environ["DAMAI_TEST_JAVA_PROGRAM_ID"])
        cases = (
            ("search_programs", {"keyword": os.environ["DAMAI_TEST_JAVA_KEYWORD"]}),
            ("get_program_detail", {"programId": program_id}),
            ("list_ticket_categories", {"programId": program_id}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name):
                result = await self.registry.execute(
                    name, arguments, self.context, timeout_seconds=8
                )
                self.assertTrue(result.success, f"{name} failed with code {result.code}")
                self.assertEqual(result.code, 0)
                self.assertIsNotNone(result.data)

    async def test_invalid_arguments_are_stopped_before_network(self) -> None:
        result = await self.registry.execute(
            "get_program_detail",
            {"programId": "not-an-id"},
            self.context,
            timeout_seconds=8,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error_code, AgentErrorCode.TOOL_ARGUMENT_INVALID)

    async def test_wrong_gateway_key_is_rejected(self) -> None:
        wrong = ToolRegistry(
            build_java_tools(
                JavaToolClient(
                    os.environ["DAMAI_TEST_JAVA_BASE_URL"],
                    os.environ["DAMAI_TEST_JAVA_TOOL_API_KEY"] + "-invalid",
                    timeout_seconds=8,
                )
            )
        )
        result = await wrong.execute(
            "search_programs",
            {"keyword": os.environ["DAMAI_TEST_JAVA_KEYWORD"]},
            self.context,
            timeout_seconds=8,
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, 401)
