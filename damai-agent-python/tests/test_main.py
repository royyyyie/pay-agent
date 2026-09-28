from __future__ import annotations

import asyncio
import signal
import unittest
from unittest.mock import Mock, patch

import uvicorn

from damai_agent.config import Settings
from damai_agent.lifecycle import LifecycleState, ServiceLifecycle
from damai_agent.main import DrainingServer, run


class MainTest(unittest.TestCase):
    def test_bootstrap_passes_one_validated_app_and_limits_to_uvicorn(self) -> None:
        settings = Settings(
            host="0.0.0.0",
            port=9011,
            shutdown_timeout_seconds=42,
            max_concurrent_requests=321,
        )
        app = Mock()
        app.state.lifecycle = ServiceLifecycle()
        server = Mock()
        with (
            patch("damai_agent.main.Settings.from_env", return_value=settings),
            patch("damai_agent.main.create_app", return_value=app) as create_app,
            patch("damai_agent.main.uvicorn.Config") as config,
            patch("damai_agent.main.DrainingServer", return_value=server) as server_type,
        ):
            run()

        create_app.assert_called_once_with(settings)
        config.assert_called_once_with(
            app,
            host="0.0.0.0",
            port=9011,
            server_header=False,
            limit_concurrency=321,
            timeout_graceful_shutdown=42,
        )
        server_type.assert_called_once_with(config.return_value, app.state.lifecycle)
        server.run.assert_called_once_with()


class DrainingServerTest(unittest.IsolatedAsyncioTestCase):
    async def test_signal_begins_application_drain_before_server_shutdown(self) -> None:
        lifecycle = ServiceLifecycle()
        config = uvicorn.Config(lambda scope, receive, send: None)
        server = DrainingServer(config, lifecycle)

        server.handle_exit(signal.SIGTERM, None)
        await asyncio.sleep(0)

        self.assertEqual(lifecycle.state, LifecycleState.DRAINING)
        self.assertTrue(server.should_exit)


if __name__ == "__main__":
    unittest.main()
