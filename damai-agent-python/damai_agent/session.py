"""In-memory conversation storage for the first runnable version."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import AsyncIterator, Dict, List

from .models import ChatMessage


class InMemorySessionStore:
    def __init__(self, max_messages: int = 40) -> None:
        self._messages: Dict[str, List[ChatMessage]] = defaultdict(list)
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()
        self._max_messages = max_messages

    async def get(self, session_key: str) -> List[ChatMessage]:
        return list(self._messages.get(session_key, []))

    async def append(self, session_key: str, messages: List[ChatMessage]) -> None:
        history = self._messages[session_key]
        history.extend(messages)
        if len(history) > self._max_messages:
            del history[: len(history) - self._max_messages]

    @asynccontextmanager
    async def turn_lock(self, session_key: str) -> AsyncIterator[None]:
        async with self._guard:
            lock = self._locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            yield
