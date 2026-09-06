"""Test doubles. No test in this suite ever reaches the real network or
requires a real Telegram bot token."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from reporter.telegram import TelegramClient


@dataclass
class SentMessage:
    chat_id: str
    text: str
    reply_to_message_id: int | None


class FakeTelegramClient(TelegramClient):
    """Overrides only `_call` -- send_message/get_updates on the real base
    class still run, so the exact same code path is exercised as
    production, just without a network call."""

    def __init__(self) -> None:
        super().__init__(bot_token="fake-token-for-tests-only")
        self.sent: list[SentMessage] = []
        self._next_message_id = 1000
        self._queued_updates: list[dict[str, Any]] = []

    def queue_update(self, update: dict[str, Any]) -> None:
        self._queued_updates.append(update)

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        if method == "sendMessage":
            message_id = self._next_message_id
            self._next_message_id += 1
            self.sent.append(SentMessage(
                chat_id=payload["chat_id"], text=payload["text"],
                reply_to_message_id=payload.get("reply_to_message_id"),
            ))
            return {"message_id": message_id}
        if method == "getUpdates":
            batch, self._queued_updates = self._queued_updates, []
            return batch
        raise AssertionError(f"unexpected Telegram method in test: {method}")
