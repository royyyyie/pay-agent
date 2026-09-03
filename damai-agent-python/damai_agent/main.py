"""Command line entry point for the Agent HTTP service."""

from __future__ import annotations

import uvicorn

from .config import Settings


def run() -> None:
    settings = Settings.from_env()
    settings.validate()
    uvicorn.run(
        "damai_agent.api:app",
        host=settings.host,
        port=settings.port,
        reload=False,
    )


if __name__ == "__main__":
    run()

