"""Production process entrypoint for the GitHub adapter on Render.

The public receiver and the required Agent Tasks poller share one service and
one persistent adapter-state database. The poller remains the existing
``poll_runner`` implementation; this module only supplies the process
supervision needed by a single Render web service.
"""
from __future__ import annotations

import threading

import uvicorn

from .adapter import GitHubCloudAdapter
from .config import get_settings
from .poll_runner import main as poll_main
from .server import create_app


def build_app():
    settings = get_settings()
    adapter = GitHubCloudAdapter(settings)
    return create_app(adapter=adapter, settings=settings), settings


def run() -> None:
    app, settings = build_app()
    threading.Thread(target=poll_main, args=([],), name="github-agent-poller", daemon=True).start()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
