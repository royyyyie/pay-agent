from __future__ import annotations

import asyncio
import json
import time
import unittest
from typing import Any, AsyncIterator, Dict, Iterator, Sequence
from unittest.mock import patch

from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamEventType,
    ProviderUsage,
    ToolContext,
    ToolResult,
    ToolSpec,
)
from damai_agent.providers import (
    OpenAICompatibleProvider,
    ProviderError,
    ProviderStreamAccumulator,
)
from damai_agent.runner import AgentRunner
from damai_agent.session import InMemorySessionStore
from damai_agent.tools import AgentTool, ToolRegistry


class FakeStreamResponse:
    status = 200
    headers: Dict[str, str] = {}

    def __init__(self, lines: Sequence[bytes], delay_seconds: float = 0) -> None:
        self._lines = lines
        self._delay_seconds = delay_seconds

    def __enter__(self) -> "FakeStreamResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __iter__(self) -> Iterator[bytes]:
        if self._delay_seconds:
            time.sleep(self._delay_seconds)
        return iter(self._lines)


class RecordingTool(AgentTool):
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="search_programs",
            description="search",
            parameters={"type": "object", "properties": {}},
        )

    async def execute(self, arguments: Dict[str, Any], context: ToolContext) -> ToolResult:
        return ToolResult(success=True, code=0, message="success", data=arguments)


class StreamingProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        raise AssertionError("streaming provider must not fall back to complete")

    async def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ProviderStreamEvent]:
        self.calls += 1
        if self.calls == 1:
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.TOOL_CALL_DELTA,
                tool_call_id="call-1",
                tool_name="search_programs",
                arguments_delta='{"keyword":',
                model_route="test/stream",
            )
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.TOOL_CALL_DELTA,
                arguments_delta='"周杰伦"}',
                model_route="test/stream",
            )
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.USAGE,
                usage=ProviderUsage(prompt_tokens=5, completion_tokens=2),
                model_route="test/stream",
            )
            yield ProviderStreamEvent(
                event_type=ProviderStreamEventType.COMPLETED,
                finish_reason="tool_calls",
                model_route="test/stream",
            )
            return
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.TEXT_DELTA,
            text_delta="找到：",
            model_route="test/stream",
        )
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.TEXT_DELTA,
            text_delta="周杰伦演唱会",
            model_route="test/stream",
        )
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.USAGE,
            usage=ProviderUsage(prompt_tokens=8, completion_tokens=3),
            model_route="test/stream",
        )
        yield ProviderStreamEvent(
            event_type=ProviderStreamEventType.COMPLETED,
            finish_reason="stop",
            model_route="test/stream",
        )


class ProviderStreamingTest(unittest.IsolatedAsyncioTestCase):
    async def test_openai_stream_assembles_text_tool_fragments_usage_and_finish(self) -> None:
        chunks = [
            {
                "model": "model-stream",
                "choices": [
                    {
                        "delta": {
                            "content": "正在查询",
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call-1",
                                    "function": {
                                        "name": "search_programs",
                                        "arguments": '{"key',
                                    },
                                }
                            ],
                        },
                        "finish_reason": None,
                    }
                ],
            },
            {
                "model": "model-stream",
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "function": {"arguments": 'word":"周杰伦"}'},
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            },
            {
                "model": "model-stream",
                "choices": [],
                "usage": {"prompt_tokens": 12, "completion_tokens": 4},
            },
        ]
        lines = [
            f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode() for chunk in chunks
        ] + [b"data: [DONE]\n\n"]
        response = FakeStreamResponse(lines)
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="test-key",
            model="configured-model",
            timeout_seconds=1,
            stream_idle_timeout_seconds=0.2,
        )

        with patch("damai_agent.providers.urllib.request.urlopen", return_value=response) as call:
            events = [
                event
                async for event in provider.stream([ChatMessage(role="user", content="周杰伦")], [])
            ]

        accumulator = ProviderStreamAccumulator()
        for event in events:
            accumulator.add(event)
        result = accumulator.build()
        request = call.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))

        self.assertTrue(payload["stream"])
        self.assertTrue(payload["stream_options"]["include_usage"])
        self.assertEqual(result.content, "正在查询")
        self.assertEqual(result.tool_calls[0].arguments, {"keyword": "周杰伦"})
        self.assertEqual(result.usage.total_tokens, 16)
        self.assertEqual(result.finish_reason, "tool_calls")
        self.assertEqual(result.model_route, "openai-compatible/model-stream")

    async def test_openai_stream_enforces_idle_timeout(self) -> None:
        response = FakeStreamResponse([b"data: [DONE]\n\n"], delay_seconds=0.1)
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="test-key",
            model="model-a",
            timeout_seconds=1,
            stream_idle_timeout_seconds=0.02,
        )

        with patch("damai_agent.providers.urllib.request.urlopen", return_value=response):
            with self.assertRaisesRegex(ProviderError, "空闲超时"):
                async for _ in provider.stream([ChatMessage(role="user", content="hi")], []):
                    pass

        await asyncio.sleep(0.11)

    async def test_openai_stream_rejects_premature_eof(self) -> None:
        response = FakeStreamResponse([b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'])
        provider = OpenAICompatibleProvider(
            base_url="https://model.example/v1",
            api_key="test-key",
            model="model-a",
            timeout_seconds=1,
        )

        with patch("damai_agent.providers.urllib.request.urlopen", return_value=response):
            events = []
            with self.assertRaisesRegex(ProviderError, "提前中断") as raised:
                async for event in provider.stream([ChatMessage(role="user", content="hi")], []):
                    events.append(event)

        self.assertTrue(raised.exception.retryable)
        self.assertEqual([event.text_delta for event in events], ["partial"])

    async def test_runner_emits_real_stream_deltas_and_usage(self) -> None:
        runner = AgentRunner(
            provider=StreamingProvider(),
            registry=ToolRegistry([RecordingTool()]),
            sessions=InMemorySessionStore(),
        )
        events: list[Dict[str, Any]] = []

        result = await runner.run("查询周杰伦", "stream-session", events.append)

        self.assertEqual(result.answer, "找到：周杰伦演唱会")
        self.assertEqual(result.tool_calls, ["search_programs"])
        self.assertEqual(result.usage.prompt_tokens, 13)
        self.assertEqual(result.usage.completion_tokens, 5)
        event_types = [event["type"] for event in events]
        self.assertIn("model.tool_call.delta", event_types)
        self.assertEqual(
            "".join(event["delta"] for event in events if event["type"] == "model.text.delta"),
            result.answer,
        )
        self.assertEqual(event_types.count("model.usage"), 2)
        self.assertEqual(
            [event["eventSeq"] for event in events],
            list(range(1, len(events) + 1)),
        )


if __name__ == "__main__":
    unittest.main()
