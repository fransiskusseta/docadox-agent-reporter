"""Cloud-owned Telegram inbound reply polling.

This poller uses the cloud Gateway store for the Telegram offset, update
deduplication, and outbound-message correlation. It only creates durable
Owner instructions; it never executes them.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from reporter import security
from reporter.config import Settings
from reporter.telegram import TelegramClient, TelegramError

from .models import OwnerReply
from .store import GatewayStore

logger = logging.getLogger("gateway.telegram_poller")


class CloudTelegramPoller:
    def __init__(self, store: GatewayStore, settings: Settings, telegram: Optional[TelegramClient]) -> None:
        self.store = store
        self.settings = settings
        self.telegram = telegram
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._fatal_error: Optional[str] = None

    @property
    def healthy(self) -> bool:
        return self._fatal_error is None and self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.telegram is None or not self.settings.telegram_configured():
            self._fatal_error = "telegram_inbound_not_configured"
            return
        self._thread = threading.Thread(target=self.run, name="gateway-telegram-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                try:
                    self.poll_once()
                    self._stop.wait(self.settings.poll_interval_sec)
                except TelegramError:
                    logger.exception("Telegram inbound poll failed; retrying")
                    self._stop.wait(self.settings.poll_interval_sec)
        except Exception as exc:
            self._fatal_error = type(exc).__name__
            logger.exception("Telegram inbound poller stopped unexpectedly")

    def poll_once(self) -> int:
        if self.telegram is None:
            raise TelegramError("Telegram inbound polling is not configured")
        updates = self.telegram.get_updates(offset=self.store.telegram_offset(), timeout=0)
        for update in updates:
            self._consume(update)
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                self.store.set_telegram_offset(update_id + 1)
        return len(updates)

    def _consume(self, update: dict) -> None:
        update_id = update.get("update_id")
        if not isinstance(update_id, int):
            return
        message = update.get("message")
        if not isinstance(message, dict):
            self.store.consume_telegram_update(update_id)
            return
        chat_id = str((message.get("chat") or {}).get("id", ""))
        if chat_id != str(self.settings.chat_id):
            self.store.consume_telegram_update(update_id)
            return
        text = message.get("text")
        reply_to = message.get("reply_to_message") or {}
        telegram_message_id = reply_to.get("message_id")
        target = self.store.telegram_message_target(telegram_message_id) if isinstance(telegram_message_id, int) else None
        if not isinstance(text, str) or not text or target is None:
            self.store.consume_telegram_update(update_id)
            return
        if security.find_secret_like_pattern(text) is not None:
            self.store.consume_telegram_update(update_id)
            return
        instruction = OwnerReply(
            message_id=f"telegram-reply:{update_id}",
            agent_id=target["agent_id"], task_id=target["task_id"], text=text,
            source_message_id=target["source_message_id"],
        ).model_dump()
        self.store.consume_telegram_update(update_id, instruction)
