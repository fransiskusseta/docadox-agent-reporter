from __future__ import annotations

import uvicorn

from .api import create_app
from .config import GatewaySettings
from .notifier import ReporterCoreTelegramNotifier
from .store import GatewayStore
from .telegram_poller import CloudTelegramPoller


def build_app():
    settings = GatewaySettings()
    store = GatewayStore(settings.db_path)
    from reporter.config import DB_PATH, Settings
    from reporter.store import Store
    from reporter.telegram import TelegramClient

    core_settings = Settings()
    core_store = Store(DB_PATH)
    telegram = TelegramClient(core_settings.bot_token) if core_settings.telegram_configured() else None
    notifier = ReporterCoreTelegramNotifier(
        settings, core_store=core_store, core_settings=core_settings, telegram=telegram,
    ) if telegram is not None else None
    poller = CloudTelegramPoller(store, core_settings, telegram)
    app = create_app(store=store, settings=settings, notifier=notifier)
    app.state.telegram_poller = poller
    return app, settings


def run() -> None:
    app, settings = build_app()
    poller = app.state.telegram_poller
    poller.start()
    try:
        uvicorn.run(app, host=settings.host, port=settings.port, log_level="info")
    finally:
        poller.stop()


if __name__ == "__main__":
    run()
