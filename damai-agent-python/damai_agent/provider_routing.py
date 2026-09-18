"""Bounded model retries and failover without replaying emitted stream events."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator, Protocol, Sequence

from .models import ChatMessage, ProviderResponse, ProviderStreamEvent, ToolSpec
from .providers import ProviderError


class StreamingModelProvider(Protocol):
    @property
    def route_name(self) -> str: ...

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse: ...

    def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ProviderStreamEvent]: ...


class ResilientProvider:
    """Retry transient primary failures, then try one fallback before any stream output."""

    def __init__(
        self,
        primary: StreamingModelProvider,
        fallback: StreamingModelProvider | None = None,
        *,
        max_retries: int = 0,
        backoff_seconds: float = 0.2,
    ) -> None:
        if not 0 <= max_retries <= 3:
            raise ValueError("max_retries must be between 0 and 3")
        if not 0 <= backoff_seconds <= 2:
            raise ValueError("backoff_seconds must be between 0 and 2")
        self._primary = primary
        self._fallback = fallback
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds

    @property
    def route_name(self) -> str:
        return self._primary.route_name

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> ProviderResponse:
        for attempt in range(self._max_retries + 1):
            try:
                return await self._primary.complete(messages, tools)
            except ProviderError as error:
                if not error.retryable:
                    raise
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * 2**attempt)
                elif self._fallback is not None:
                    return await self._fallback.complete(messages, tools)
                else:
                    raise
        raise AssertionError("primary retry loop did not terminate")

    async def stream(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec]
    ) -> AsyncIterator[ProviderStreamEvent]:
        for attempt in range(self._max_retries + 1):
            emitted = False
            try:
                async for event in self._primary.stream(messages, tools):
                    emitted = True
                    yield event
                return
            except ProviderError as error:
                if emitted or not error.retryable:
                    raise
                if attempt < self._max_retries:
                    await asyncio.sleep(self._backoff_seconds * 2**attempt)
                elif self._fallback is not None:
                    async for event in self._fallback.stream(messages, tools):
                        yield event
                    return
                else:
                    raise
        raise AssertionError("primary retry loop did not terminate")
