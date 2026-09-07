"""Runs the localhost API and the Telegram inbound poller together, with
graceful shutdown on SIGINT/SIGTERM.
"""
from __future__ import annotations

import logging
import signal
import sys

import uvicorn

from .api import create_app
from .config import DB_PATH, get_settings
from .poller import PollerThread
from .store import Store
from .telegram import TelegramClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("reporter.service")


def run() -> None:
    settings = get_settings()
    store = Store(DB_PATH)
    telegram = TelegramClient(settings.bot_token) if settings.telegram_configured() else None
    if telegram is None:
        logger.warning(
            "DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN/CHAT_ID not set -- running with Telegram delivery "
            "disabled. Events are still recorded locally; nothing will be sent to Telegram."
        )

    if not settings.local_telegram_poller_enabled():
        logger.info("Local Telegram polling disabled (cloud Gateway owns inbound polling)")

    app = create_app(store=store, settings=settings, telegram=telegram)
    poller = PollerThread(store, settings, telegram) if settings.local_telegram_poller_enabled() else None
    if poller is not None:
        poller.start()

    config = uvicorn.Config(app, host=settings.host, port=settings.port, log_level="info")
    server = uvicorn.Server(config)

    def _shutdown(signum, frame):
        logger.info("Shutdown signal received; stopping poller and server…")
        if poller is not None:
            poller.stop()
        server.should_exit = True

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    logger.info("Docadox Agent Reporter listening on %s:%s (loopback-only)", settings.host, settings.port)
    server.run()
    if poller is not None:
        poller.stop()
        poller.join(timeout=5)


if __name__ == "__main__":
    sys.exit(run() or 0)
