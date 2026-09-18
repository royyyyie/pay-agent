from __future__ import annotations

import unittest
from typing import Any, Dict

from damai_agent.models import (
    AgentErrorCode,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from damai_agent.tools import AgentTool, ToolCallLedger, ToolRegistry


class RecordingTool(AgentTool):
    def __init__(self, risk: ToolRisk = ToolRisk.READ_ONLY) -> None:
        self.calls = 0
        self.arguments: Dict[str, Any] = {}
        self._spec = ToolSpec(
            name="program_lookup",
            description="lookup",
            parameters={
                "type": "object",
                "required": ["programId"],
                "additionalProperties": False,
                "properties": {
                    "programId": {"type": "integer", "minimum": 1},
                    "pageSize": {"type": "integer", "minimum": 1, "maximum": 20, "default": 10},
                },
            },
            risk=risk,
            required_scope="programs:read",
            max_calls_per_turn=2,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        self.calls += 1
        self.arguments = arguments
        return ToolResult(success=True, code=0, message="ok")


def context(
    scopes: frozenset[str] = frozenset({"programs:read"}),
    risk_ceiling: ToolRisk = ToolRisk.READ_ONLY,
) -> ToolContext:
    return ToolContext(
        session_key="session-policy",
        turn_id="turn-policy",
        tool_call_id="call-policy",
        trace_id="a" * 32,
        tool_scopes=scopes,
        risk_ceiling=risk_ceiling,
    )


class ToolPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_safe_conversion_and_defaults_happen_before_execution(self) -> None:
        tool = RecordingTool()
        registry = ToolRegistry([tool])

        result = await registry.execute(
            "program_lookup",
            {"programId": "1001"},
            context(),
            timeout_seconds=1,
        )

        self.assertTrue(result.success)
        self.assertEqual(tool.arguments, {"programId": 1001, "pageSize": 10})

    async def test_invalid_arguments_never_reach_tool(self) -> None:
        tool = RecordingTool()
        registry = ToolRegistry([tool])

        result = await registry.execute(
            "program_lookup",
            {"programId": 0, "unexpected": "value"},
            context(),
            timeout_seconds=1,
        )

        self.assertFalse(result.success)
        self.assertEqual(result.error_code, AgentErrorCode.TOOL_ARGUMENT_INVALID)
        self.assertEqual(tool.calls, 0)
        self.assertNotIn("value", result.message)

    async def test_missing_scope_is_denied(self) -> None:
        tool = RecordingTool()
        result = await ToolRegistry([tool]).execute(
            "program_lookup",
            {"programId": 1001},
            context(frozenset()),
            timeout_seconds=1,
        )

        self.assertEqual(result.code, 403)
        self.assertEqual(result.error_code, AgentErrorCode.TOOL_SCOPE_DENIED)
        self.assertEqual(tool.calls, 0)

    async def test_risk_above_ceiling_is_denied(self) -> None:
        tool = RecordingTool(ToolRisk.REVERSIBLE_WRITE)
        result = await ToolRegistry([tool]).execute(
            "program_lookup",
            {"programId": 1001},
            context(risk_ceiling=ToolRisk.READ_ONLY),
            timeout_seconds=1,
        )

        self.assertEqual(result.code, 403)
        self.assertEqual(result.error_code, AgentErrorCode.TOOL_RISK_DENIED)
        self.assertEqual(tool.calls, 0)

    async def test_per_tool_call_limit_is_enforced(self) -> None:
        tool = RecordingTool()
        registry = ToolRegistry([tool])
        ledger = ToolCallLedger(max_calls=10)

        for _ in range(2):
            result = await registry.execute(
                "program_lookup",
                {"programId": 1001},
                context(),
                timeout_seconds=1,
                ledger=ledger,
            )
            self.assertTrue(result.success)
        rejected = await registry.execute(
            "program_lookup",
            {"programId": 1001},
            context(),
            timeout_seconds=1,
            ledger=ledger,
        )

        self.assertEqual(rejected.code, 429)
        self.assertEqual(rejected.error_code, AgentErrorCode.TOOL_CALL_LIMIT_EXCEEDED)
        self.assertEqual(tool.calls, 2)

    async def test_global_turn_call_limit_is_enforced(self) -> None:
        tool = RecordingTool()
        registry = ToolRegistry([tool])
        ledger = ToolCallLedger(max_calls=1)

        first = await registry.execute(
            "program_lookup",
            {"programId": 1001},
            context(),
            timeout_seconds=1,
            ledger=ledger,
        )
        second = await registry.execute(
            "program_lookup",
            {"programId": 1002},
            context(),
            timeout_seconds=1,
            ledger=ledger,
        )

        self.assertTrue(first.success)
        self.assertEqual(second.error_code, AgentErrorCode.TOOL_CALL_LIMIT_EXCEEDED)
        self.assertEqual(tool.calls, 1)


if __name__ == "__main__":
    unittest.main()
