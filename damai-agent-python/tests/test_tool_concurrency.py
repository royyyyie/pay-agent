from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any, Dict, Sequence

from damai_agent.models import (
    AgentRunSpec,
    ChatMessage,
    ProviderResponse,
    TicketTurnContext,
    ToolCall,
    ToolContext,
    ToolResult,
    ToolRisk,
    ToolSpec,
)
from damai_agent.runner import AgentRunner, ToolCallingRunner
from damai_agent.runtime.events import TurnEventEmitter
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class Activity:
    def __init__(self) -> None:
        self.active = 0
        self.peak = 0
        self.timeline: list[tuple[str, str, int]] = []


class TrackedTool(AgentTool):
    def __init__(
        self,
        name: str,
        activity: Activity,
        *,
        concurrency_safe: bool = False,
        exclusive: bool = False,
        fail_call: str = "",
        risk: ToolRisk = ToolRisk.READ_ONLY,
        slow_seconds: float = 0.02,
        fast_seconds: float = 0.004,
    ) -> None:
        self._activity = activity
        self._fail_call = fail_call
        self._slow_seconds = slow_seconds
        self._fast_seconds = fast_seconds
        self._spec = ToolSpec(
            name=name,
            description="test tool",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            concurrency_safe=concurrency_safe,
            exclusive=exclusive,
            risk=risk,
            max_calls_per_turn=12,
        )

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        activity = self._activity
        activity.active += 1
        activity.peak = max(activity.peak, activity.active)
        activity.timeline.append(("start", context.tool_call_id, activity.active))
        try:
            # Complete later calls first to verify that protocol order is preserved.
            await asyncio.sleep(
                self._slow_seconds if context.tool_call_id.endswith("0") else self._fast_seconds
            )
            if context.tool_call_id == self._fail_call:
                raise RuntimeError("unexpected upstream detail")
            return ToolResult(success=True, code=0, message="ok", data=context.tool_call_id)
        finally:
            activity.timeline.append(("end", context.tool_call_id, activity.active))
            activity.active -= 1


class BatchProvider:
    def __init__(self, calls: list[ToolCall]) -> None:
        self._calls = calls
        self.observations: list[ChatMessage] = []
        self.offered_specs: list[ToolSpec] = []
        self.round = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.round += 1
        self.offered_specs = list(tools)
        if self.round == 1:
            return ProviderResponse(tool_calls=self._calls, finish_reason="tool_calls")
        self.observations = [message for message in messages if message.role == "tool"]
        return ProviderResponse(content="done")


def make_calls(names: Sequence[str]) -> list[ToolCall]:
    return [
        ToolCall(id=f"call-{index}", name=name, arguments={}) for index, name in enumerate(names)
    ]


class ToolConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def test_runner_rejects_unbounded_concurrency(self) -> None:
        with self.assertRaises(ValueError):
            ToolCallingRunner(BatchProvider([]), ToolRegistry(), max_concurrent_read_tools=13)

    async def test_only_adjacent_safe_reads_overlap_and_results_keep_call_order(self) -> None:
        activity = Activity()
        names = ["read", "read", "exclusive", "read", "serial", "read", "read"]
        provider = BatchProvider(make_calls(names))
        tools = [
            TrackedTool("read", activity, concurrency_safe=True),
            TrackedTool("exclusive", activity, exclusive=True),
            TrackedTool("serial", activity),
        ]
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(tools),
            sessions=InMemorySessionStore(),
            max_concurrent_read_tools=2,
        )
        events: list[Dict[str, Any]] = []

        result = await runner.run("lookup", "parallel-session", events.append)

        self.assertEqual(result.answer, "done")
        self.assertEqual(result.tool_calls, names)
        self.assertEqual(activity.peak, 2)
        self.assertEqual(
            [m.tool_call_id for m in provider.observations],
            [f"call-{index}" for index in range(len(names))],
        )
        self.assertEqual(
            [json.loads(message.content or "{}")["data"] for message in provider.observations],
            [f"call-{index}" for index in range(len(names))],
        )
        for name, index in (("exclusive", 2), ("serial", 4)):
            self.assertEqual(name, names[index])
            self.assertEqual(activity.timeline[index * 2][1], f"call-{index}")
            self.assertEqual(activity.timeline[index * 2][2], 1)
        completed = [event for event in events if event["type"] == "tool.completed"]
        self.assertEqual(
            [event["toolCallId"] for event in completed],
            [f"call-{index}" for index in range(len(names))],
        )
        self.assertEqual([event["eventSeq"] for event in events], list(range(1, len(events) + 1)))
        self.assertTrue(all(event["traceId"] == result.trace_id for event in events))

    async def test_parallel_failures_and_budget_rejections_still_return_one_result_each(
        self,
    ) -> None:
        activity = Activity()
        provider = BatchProvider(make_calls(["read", "read", "read"]))
        registry = ToolRegistry(
            [TrackedTool("read", activity, concurrency_safe=True, fail_call="call-1")]
        )
        runner = AgentRunner(
            provider=provider,
            registry=registry,
            sessions=InMemorySessionStore(),
            max_concurrent_read_tools=2,
            max_tool_calls=2,
        )

        result = await runner.run("lookup", "parallel-failure-session")

        self.assertEqual(result.tool_calls, ["read", "read", "read"])
        self.assertEqual(activity.peak, 2)
        self.assertEqual(
            [json.loads(message.content or "{}")["errorCode"] for message in provider.observations],
            [None, "TOOL_EXECUTION_FAILED", "TOOL_CALL_LIMIT_EXCEEDED"],
        )
        self.assertNotIn(
            "unexpected upstream detail",
            " ".join(message.content or "" for message in provider.observations),
        )

    async def test_untrusted_runspec_cannot_reclassify_registered_exclusive_tool(self) -> None:
        activity = Activity()
        tool = TrackedTool("exclusive", activity, exclusive=True)
        provider = BatchProvider(make_calls(["exclusive", "exclusive"]))
        runner = ToolCallingRunner(provider, ToolRegistry([tool]), max_concurrent_read_tools=2)
        context = TicketTurnContext(
            tenant_id="local",
            user_id="user",
            session_key="s",
            turn_id="t",
            request_id="r",
            trace_id="a" * 32,
            locale="zh-CN",
            channel="test",
            tool_scopes=frozenset(),
            risk_ceiling=ToolRisk.READ_ONLY,
            delegation_token_id="test",
        )
        forged = ToolSpec(
            name="exclusive",
            description="forged",
            parameters={},
            concurrency_safe=True,
            exclusive=False,
        )
        spec = AgentRunSpec(
            context=context,
            messages=(ChatMessage(role="user", content="lookup"),),
            tool_specs=(forged,),
            system_prompt="test",
            prompt_version="1",
            toolset_version="1",
            policy_version="1",
            model_route="test",
        )

        result = await runner.run(spec, TurnEventEmitter(context, None))

        self.assertEqual(result.final_content, "done")
        self.assertEqual(activity.peak, 1)
        self.assertIs(provider.offered_specs[0], tool.spec)

    async def test_timeout_does_not_discard_other_parallel_tool_results(self) -> None:
        activity = Activity()
        provider = BatchProvider(make_calls(["read", "read"]))
        runner = AgentRunner(
            provider=provider,
            registry=ToolRegistry(
                [
                    TrackedTool(
                        "read",
                        activity,
                        concurrency_safe=True,
                        slow_seconds=0.25,
                        fast_seconds=0.01,
                    )
                ]
            ),
            sessions=InMemorySessionStore(),
            max_concurrent_read_tools=2,
            tool_timeout_seconds=0.1,
        )

        await runner.run("lookup", "parallel-timeout-session")

        self.assertEqual(activity.active, 0)
        self.assertEqual(
            [json.loads(message.content or "{}")["errorCode"] for message in provider.observations],
            ["TOOL_TIMEOUT", None],
        )

    async def test_write_risk_never_parallelizes_even_if_marked_safe(self) -> None:
        activity = Activity()
        tool = TrackedTool("write", activity, concurrency_safe=True, risk=ToolRisk.REVERSIBLE_WRITE)
        provider = BatchProvider(make_calls(["write", "write"]))
        runner = ToolCallingRunner(provider, ToolRegistry([tool]), max_concurrent_read_tools=2)
        context = TicketTurnContext(
            tenant_id="local",
            user_id="user",
            session_key="s",
            turn_id="t",
            request_id="r",
            trace_id="b" * 32,
            locale="zh-CN",
            channel="test",
            tool_scopes=frozenset(),
            risk_ceiling=ToolRisk.REVERSIBLE_WRITE,
            delegation_token_id="test",
        )
        spec = AgentRunSpec(
            context=context,
            messages=(ChatMessage(role="user", content="lookup"),),
            tool_specs=(tool.spec,),
            system_prompt="test",
            prompt_version="1",
            toolset_version="1",
            policy_version="1",
            model_route="test",
        )

        await runner.run(spec, TurnEventEmitter(context, None))

        self.assertEqual(activity.peak, 1)
