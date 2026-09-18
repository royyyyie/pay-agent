from __future__ import annotations

import asyncio
import io
import unittest
import urllib.error
from typing import AsyncIterator, Sequence
from unittest.mock import patch

from damai_agent.models import (
    ChatMessage,
    ProviderResponse,
    ProviderStreamEvent,
    ProviderStreamEventType,
    ToolSpec,
)
from damai_agent.provider_routing import ResilientProvider
from damai_agent.providers import OpenAICompatibleProvider, ProviderError


class ScriptedProvider:
    def __init__(
        self,
        route_name: str,
        complete_outcomes: list[ProviderResponse | ProviderError] | None = None,
        stream_outcomes: list[list[ProviderStreamEvent | ProviderError]] | None = None,
    ) -> None:
        self.route_name = route_name
        self.complete_outcomes = complete_outcomes or []
        self.stream_outcomes = stream_outcomes or []
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        self.complete_calls += 1
        outcome = self.complete_outcomes.pop(0)
        if isinstance(outcome, ProviderError):
            raise outcome
        return outcome

    async def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ProviderStreamEvent]:
        self.stream_calls += 1
        for outcome in self.stream_outcomes.pop(0):
            if isinstance(outcome, ProviderError):
                raise outcome
            yield outcome


class ProviderRoutingTest(unittest.IsolatedAsyncioTestCase):
    async def test_complete_retries_then_falls_back_once(self) -> None:
        transient = ProviderError("temporary", retryable=True)
        primary = ScriptedProvider("primary", [transient, transient])
        fallback = ScriptedProvider(
            "fallback", [ProviderResponse(content="备用回答", model_route="fallback")]
        )
        provider = ResilientProvider(primary, fallback, max_retries=1, backoff_seconds=0)

        result = await provider.complete([], [])

        self.assertEqual(result.content, "备用回答")
        self.assertEqual(result.model_route, "fallback")
        self.assertEqual(primary.complete_calls, 2)
        self.assertEqual(fallback.complete_calls, 1)

    async def test_permanent_failure_never_retries_or_falls_back(self) -> None:
        primary = ScriptedProvider("primary", [ProviderError("unauthorized")])
        fallback = ScriptedProvider("fallback")
        provider = ResilientProvider(primary, fallback, max_retries=2, backoff_seconds=0)

        with self.assertRaisesRegex(ProviderError, "unauthorized"):
            await provider.complete([], [])

        self.assertEqual(primary.complete_calls, 1)
        self.assertEqual(fallback.complete_calls, 0)

    async def test_stream_falls_back_only_before_first_event(self) -> None:
        primary = ScriptedProvider(
            "primary", stream_outcomes=[[ProviderError("temporary", retryable=True)]]
        )
        event = ProviderStreamEvent(
            event_type=ProviderStreamEventType.TEXT_DELTA,
            text_delta="备用回答",
            model_route="fallback",
        )
        fallback = ScriptedProvider("fallback", stream_outcomes=[[event]])
        provider = ResilientProvider(primary, fallback, backoff_seconds=0)

        events = [item async for item in provider.stream([], [])]

        self.assertEqual(events, [event])
        self.assertEqual(primary.stream_calls, 1)
        self.assertEqual(fallback.stream_calls, 1)

    async def test_partial_stream_is_not_replayed_or_combined_with_fallback(self) -> None:
        event = ProviderStreamEvent(
            event_type=ProviderStreamEventType.TEXT_DELTA,
            text_delta="部分文本",
            model_route="primary",
        )
        primary = ScriptedProvider(
            "primary", stream_outcomes=[[event, ProviderError("lost", retryable=True)]]
        )
        fallback = ScriptedProvider("fallback")
        provider = ResilientProvider(primary, fallback, max_retries=2, backoff_seconds=0)
        observed = []

        with self.assertRaisesRegex(ProviderError, "lost"):
            async for item in provider.stream([], []):
                observed.append(item)

        self.assertEqual(observed, [event])
        self.assertEqual(primary.stream_calls, 1)
        self.assertEqual(fallback.stream_calls, 0)

    async def test_stream_retry_is_bounded(self) -> None:
        primary = ScriptedProvider(
            "primary",
            stream_outcomes=[
                [ProviderError("temporary", retryable=True)],
                [ProviderError("temporary", retryable=True)],
            ],
        )
        provider = ResilientProvider(primary, max_retries=1, backoff_seconds=0)

        with self.assertRaises(ProviderError):
            async for _ in provider.stream([], []):
                pass

        self.assertEqual(primary.stream_calls, 2)

    async def test_http_error_is_classified_without_leaking_body(self) -> None:
        model = OpenAICompatibleProvider("https://model.example/v1", "key", "m", 1)
        for status, retryable in ((429, True), (503, True), (401, False), (400, False)):
            with self.subTest(status=status):
                response = urllib.error.HTTPError(
                    "https://model.example/v1/chat/completions",
                    status,
                    "error",
                    {},
                    io.BytesIO(b"private-upstream-body"),
                )
                with patch("damai_agent.providers.urllib.request.urlopen", side_effect=response):
                    with self.assertRaises(ProviderError) as raised:
                        await model.complete([], [])
                self.assertEqual(raised.exception.retryable, retryable)
                self.assertNotIn("private-upstream-body", str(raised.exception))

    async def test_stream_http_auth_error_is_not_retryable(self) -> None:
        model = OpenAICompatibleProvider("https://model.example/v1", "key", "m", 1)
        response = urllib.error.HTTPError(
            "https://model.example/v1/chat/completions",
            401,
            "error",
            {},
            io.BytesIO(b"private-upstream-body"),
        )
        with patch("damai_agent.providers.urllib.request.urlopen", side_effect=response):
            with self.assertRaises(ProviderError) as raised:
                async for _ in model.stream([], []):
                    pass
        self.assertFalse(raised.exception.retryable)
        self.assertNotIn("private-upstream-body", str(raised.exception))

    async def test_cancelled_task_is_not_retried(self) -> None:
        class CancelledProvider(ScriptedProvider):
            async def complete(
                self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
            ) -> ProviderResponse:
                self.complete_calls += 1
                raise asyncio.CancelledError

        primary = CancelledProvider("primary")
        fallback = ScriptedProvider("fallback")
        provider = ResilientProvider(primary, fallback, max_retries=2, backoff_seconds=0)

        with self.assertRaises(asyncio.CancelledError):
            await provider.complete([], [])
        self.assertEqual(primary.complete_calls, 1)
        self.assertEqual(fallback.complete_calls, 0)
