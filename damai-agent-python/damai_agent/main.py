"""Command line entry point for the Agent HTTP service."""

from __future__ import annotations

import asyncio
from types import FrameType

import uvicorn

from .api import create_app
from .config import Settings
from .lifecycle import ServiceLifecycle


class DrainingServer(uvicorn.Server):
    """Tell the application to stop admission as soon as Uvicorn sees a signal."""

    def __init__(self, config: uvicorn.Config, lifecycle: ServiceLifecycle) -> None:
        super().__init__(config)
        self._lifecycle = lifecycle
        self._drain_task: asyncio.Task[None] | None = None

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if self._drain_task is None:
            self._drain_task = asyncio.get_running_loop().create_task(
                self._lifecycle.begin_drain(),
                name="damai-agent-begin-drain",
            )
        super().handle_exit(sig, frame)


def run() -> None:
    settings = Settings.from_env()
    app = create_app(settings)
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        server_header=False,
        limit_concurrency=settings.max_concurrent_requests,
        timeout_graceful_shutdown=int(settings.shutdown_timeout_seconds),
    )
    DrainingServer(config, app.state.lifecycle).run()


if __name__ == "__main__":
    run()
