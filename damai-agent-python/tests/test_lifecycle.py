from __future__ import annotations

import asyncio
import unittest
from typing import Any

from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from damai_agent.api import create_app
from damai_agent.config import Settings
from damai_agent.lifecycle import LifecycleState, RequestDrainMiddleware, ServiceLifecycle


class ServiceLifecycleTest(unittest.IsolatedAsyncioTestCase):
    async def test_drain_rejects_new_work_and_waits_for_admitted_work(self) -> None:
        lifecycle = ServiceLifecycle()

        self.assertTrue(await lifecycle.acquire())
        await lifecycle.begin_drain()

        self.assertEqual(lifecycle.state, LifecycleState.DRAINING)
        self.assertFalse(await lifecycle.acquire())
        self.assertFalse(await lifecycle.wait_for_idle(0.001))
        self.assertEqual(lifecycle.drain_timeouts, 1)

        await lifecycle.release()
        self.assertTrue(await lifecycle.wait_for_idle(0.1))
        await lifecycle.stop()
        self.assertEqual(lifecycle.state, LifecycleState.STOPPED)

    async def test_middleware_counts_until_streaming_response_finishes(self) -> None:
        lifecycle = ServiceLifecycle()
        started = asyncio.Event()
        finish = asyncio.Event()

        async def streaming_app(scope: Scope, receive: Receive, send: Send) -> None:
            await send({"type": "http.response.start", "status": 200, "headers": []})
            started.set()
            await finish.wait()
            await send({"type": "http.response.body", "body": b"done"})

        middleware = RequestDrainMiddleware(streaming_app, lifecycle)
        messages: list[Message] = []

        async def receive() -> Message:
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: Message) -> None:
            messages.append(message)

        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/api/v1/chat/stream",
            "raw_path": b"/api/v1/chat/stream",
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }
        task = asyncio.create_task(middleware(scope, receive, send))  # type: ignore[arg-type]
        await started.wait()

        self.assertEqual(lifecycle.inflight, 1)
        await lifecycle.begin_drain()

        rejected: list[Message] = []

        async def reject_send(message: Message) -> None:
            rejected.append(message)

        await middleware(scope, receive, reject_send)  # type: ignore[arg-type]
        self.assertEqual(rejected[0]["status"], 503)
        self.assertEqual(lifecycle.inflight, 1)

        finish.set()
        await task
        self.assertEqual(lifecycle.inflight, 0)
        self.assertEqual(messages[-1]["body"], b"done")


class LifecycleApiTest(unittest.TestCase):
    def test_standard_probes_and_drain_metrics(self) -> None:
        app = create_app(Settings())
        with TestClient(app) as client:
            live = client.get("/livez")
            ready = client.get("/readyz")

            self.assertEqual(live.status_code, 200)
            self.assertEqual(live.json(), {"status": "UP"})
            self.assertEqual(live.headers["cache-control"], "no-store")
            self.assertEqual(ready.status_code, 200)

            client.portal.call(app.state.lifecycle.begin_drain)
            self.assertEqual(client.get("/readyz").status_code, 503)
            rejected = client.post("/api/v1/chat", json={"message": "hello"})
            self.assertEqual(rejected.status_code, 503)
            self.assertEqual(rejected.json()["detail"]["code"], "SERVICE_DRAINING")
            self.assertEqual(rejected.headers["retry-after"], "5")

            metrics = client.get("/metrics").text
            self.assertIn("damai_agent_accepting_requests 0", metrics)
            self.assertIn("damai_agent_inflight_requests 0", metrics)


if __name__ == "__main__":
    unittest.main()
