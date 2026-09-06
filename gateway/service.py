from __future__ import annotations

import os

import uvicorn

from .api import create_app
from .config import GatewaySettings
from .notifier import ReporterCoreTelegramNotifier
from .store import GatewayStore


def build_app():
    settings = GatewaySettings()
    store = GatewayStore(settings.db_path)
    notifier = None
    if os.environ.get("DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN") and os.environ.get("DOCADOX_REPORTER_TELEGRAM_CHAT_ID"):
        notifier = ReporterCoreTelegramNotifier(settings)
    return create_app(store=store, settings=settings, notifier=notifier), settings


def run() -> None:
    app, settings = build_app()
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")


if __name__ == "__main__":
    run()
