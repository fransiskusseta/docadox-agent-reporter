"""Inbound Telegram polling: long-poll-free simple getUpdates loop (v1 does
not require a public webhook). Every update is either routed into an
agent's inbox or safely discarded (wrong chat, non-text, unroutable) --
the persisted offset always advances past it either way, so a restart
never reprocesses old updates.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Optional

from . import approval
from .config import Settings
from .store import Store
from .telegram import TelegramClient, TelegramError

logger = logging.getLogger("reporter.poller")


@dataclass
class RoutedReply:
    agent_id: str
    task_id: Optional[str]
    routed: bool


def _extract_text(message: dict) -> Optional[str]:
    # "no incoming Telegram attachments executed" -- only plain text is
    # ever processed; anything else (photo/document/etc.) is ignored.
    if "text" not in message:
        return None
    return message["text"]


def route_inbound_message(store: Store, chat_id_str: str, message: dict) -> RoutedReply:
    reply_to = message.get("reply_to_message")
    reply_to_id = reply_to.get("message_id") if reply_to else None

    mapped = store.resolve_telegram_message(reply_to_id) if reply_to_id else None
    if mapped is not None:
        return RoutedReply(agent_id=mapped["agent_id"], task_id=mapped["task_id"], routed=True)

    # Not a reply to a known notification -- fall back ONLY when exactly one
    # agent currently needs Owner action; otherwise this is ambiguous and
    # must not be guessed at.
    fallback = store.most_recent_action_required_agent()
    if fallback is not None:
        return RoutedReply(agent_id=fallback["agent_id"], task_id=fallback["task_id"], routed=True)

    return RoutedReply(agent_id="unrouted", task_id=None, routed=False)


def process_update(store: Store, settings: Settings, update: dict) -> None:
    message = update.get("message")
    if not message:
        return  # edited_message, channel_post, etc. -- ignored in v1.

    chat = message.get("chat", {})
    chat_id_str = str(chat.get("id", ""))
    if chat_id_str != str(settings.chat_id):
        logger.warning("Ignoring message from unconfigured chat")
        return

    text = _extract_text(message)
    if text is None:
        return  # attachment-only message: never processed/executed.

    routed = route_inbound_message(store, chat_id_str, message)
    store.add_inbox_entry(
        agent_id=routed.agent_id,
        task_id=routed.task_id,
        message_text=text,
        source_chat_id=chat_id_str,
        telegram_message_id=message.get("message_id"),
        reply_to_message_id=(message.get("reply_to_message") or {}).get("message_id"),
        contains_privileged_keyword=approval.contains_privileged_keyword(text),
        routed=routed.routed,
    )


def poll_once(store: Store, settings: Settings, telegram: TelegramClient) -> int:
    """Fetches and processes exactly one batch of updates. Returns the
    number of updates processed. Advances and persists the offset past
    every update seen, regardless of whether it was routable."""
    offset = store.get_offset()
    try:
        updates = telegram.get_updates(offset=offset, timeout=0)
    except TelegramError:
        logger.exception("Telegram getUpdates failed; will retry next cycle")
        return 0
    for update in updates:
        try:
            process_update(store, settings, update)
        except Exception:
            logger.exception("Failed to process one inbound Telegram update; skipping it")
        finally:
            store.set_offset(update["update_id"] + 1)
    return len(updates)


class PollerThread(threading.Thread):
    """Background polling loop with graceful shutdown via a stop event."""

    def __init__(self, store: Store, settings: Settings, telegram: Optional[TelegramClient]):
        super().__init__(name="reporter-telegram-poller", daemon=True)
        self._store = store
        self._settings = settings
        self._telegram = telegram
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if self._telegram is None:
            logger.info("Telegram not configured; inbound polling disabled.")
            return
        while not self._stop_event.is_set():
            poll_once(self._store, self._settings, self._telegram)
            self._stop_event.wait(self._settings.poll_interval_sec)
