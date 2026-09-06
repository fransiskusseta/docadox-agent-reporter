"""Minimal Telegram Bot API client: outbound sendMessage + inbound getUpdates
polling. Uses only the standard library (urllib) -- no third-party HTTP
dependency needed. The bot token is read once at construction and is never
logged, printed, or included in any exception message this module raises.

Tests inject a fake by subclassing TelegramClient and overriding `_call`
(see tests/fakes.py) -- nothing here reaches the real network under test.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


class TelegramError(Exception):
    """Raised for a Telegram API failure. Message text is safe (never
    includes the bot token -- the token lives only in the URL path, which
    this exception never echoes)."""


@dataclass
class TelegramClient:
    bot_token: str
    timeout: float = 10.0

    def _call(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"https://api.telegram.org/bot{self.bot_token}/{method}"
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise TelegramError(f"Telegram API request failed: {type(exc).__name__}") from exc
        if not data.get("ok"):
            raise TelegramError(f"Telegram API returned not-ok for {method}: {data.get('description', '')[:200]}")
        return data["result"]

    def send_message(self, chat_id: str, text: str, reply_to_message_id: Optional[int] = None) -> int:
        """Returns the sent message's own message_id (needed to map a future
        reply back to this exact notification)."""
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        result = self._call("sendMessage", payload)
        return result["message_id"]

    def get_updates(self, offset: int, timeout: int = 0) -> list[dict[str, Any]]:
        return self._call("getUpdates", {"offset": offset, "timeout": timeout, "allowed_updates": ["message"]})
