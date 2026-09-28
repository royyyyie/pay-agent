"""Process lifecycle and full-response request draining for production rollouts."""

from __future__ import annotations

import asyncio
import json
from enum import Enum
from typing import Callable

from starlette.types import ASGIApp, Receive, Scope, Send


class LifecycleState(str, Enum):
    READY = "ready"
    DRAINING = "draining"
    STOPPED = "stopped"


class ServiceLifecycle:
    """Track admission and in-flight requests without coupling to business handlers."""

    def __init__(self) -> None:
        self._state = LifecycleState.READY
        self._inflight = 0
        self._condition = asyncio.Condition()
        self._drain_timeouts = 0

    @property
    def state(self) -> LifecycleState:
        return self._state

    @property
    def accepting(self) -> bool:
        return self._state is LifecycleState.READY

    @property
    def inflight(self) -> int:
        return self._inflight

    @property
    def drain_timeouts(self) -> int:
        return self._drain_timeouts

    async def acquire(self) -> bool:
        async with self._condition:
            if self._state is not LifecycleState.READY:
                return False
            self._inflight += 1
            return True

    async def release(self) -> None:
        async with self._condition:
            if self._inflight <= 0:
                raise RuntimeError("lifecycle request counter underflow")
            self._inflight -= 1
            if self._inflight == 0:
                self._condition.notify_all()

    async def begin_drain(self) -> None:
        async with self._condition:
            if self._state is LifecycleState.READY:
                self._state = LifecycleState.DRAINING
            self._condition.notify_all()

    async def wait_for_idle(self, timeout_seconds: float) -> bool:
        async def wait() -> None:
            async with self._condition:
                await self._condition.wait_for(lambda: self._inflight == 0)

        try:
            await asyncio.wait_for(wait(), timeout=max(0.001, timeout_seconds))
            return True
        except TimeoutError:
            async with self._condition:
                self._drain_timeouts += 1
            return False

    async def stop(self) -> None:
        async with self._condition:
            self._state = LifecycleState.STOPPED
            self._condition.notify_all()


class RequestDrainMiddleware:
    """Reject new business traffic while preserving probes and full SSE accounting."""

    def __init__(
        self,
        app: ASGIApp,
        lifecycle: ServiceLifecycle,
        protected_prefixes: tuple[str, ...] = ("/api/",),
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self._app = app
        self._lifecycle = lifecycle
        self._protected_prefixes = protected_prefixes
        self._on_change = on_change

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = str(scope.get("path", ""))
        protected = scope["type"] == "http" and path.startswith(self._protected_prefixes)
        if not protected:
            await self._app(scope, receive, send)
            return
        if not await self._lifecycle.acquire():
            await self._reject(scope, receive, send)
            return
        if self._on_change is not None:
            self._on_change()
        try:
            await self._app(scope, receive, send)
        finally:
            await self._lifecycle.release()
            if self._on_change is not None:
                self._on_change()

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = json.dumps(
            {"detail": {"code": "SERVICE_DRAINING", "message": "服务正在排空，请稍后重试"}},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"retry-after", b"5"),
        ]
        await send(
            {
                "type": "http.response.start",
                "status": 503,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": body})
