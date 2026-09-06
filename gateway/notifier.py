from __future__ import annotations

from typing import Protocol

from .store import GatewayStore


class EventNotifier(Protocol):
    def notify(self, event: dict, store: GatewayStore) -> None: ...


class NullNotifier:
    """Explicitly records that Telegram is not attached to this process."""
    def notify(self, event: dict, store: GatewayStore) -> None:
        store.mark_event_notified(event["message_id"], "telegram_notifier_not_attached")


class ReporterCoreTelegramNotifier:
    """Adapter to Claude #1's Reporter Core notification contract.

    It reuses Reporter Core's formatter, secret guard, and Telegram client.
    Inbound replies remain Reporter Core's responsibility and arrive through
    the authenticated /v1/cloud/owner-replies contract.
    """
    def __init__(self, settings, *, core_store=None, core_settings=None, telegram=None) -> None:
        from reporter.config import DB_PATH, Settings
        from reporter.store import Store
        from reporter.telegram import TelegramClient

        self.settings = settings
        self.core_settings = core_settings or Settings()
        self.core_store = core_store or Store(DB_PATH)
        self.telegram = telegram or TelegramClient(self.core_settings.bot_token)

    def notify(self, event: dict, store: GatewayStore) -> None:
        from reporter import notifications

        try:
            result = notifications.handle_event(
                store=self.core_store, settings=self.core_settings, telegram=self.telegram,
                agent_id=event["agent_id"], agent_name=event.get("agent_name") or event["agent_id"],
                task_id=event["task_id"], status=event["status"], summary=event["summary"],
                details=event.get("details"), client_timestamp=event.get("timestamp"),
                message_id=event["message_id"],
            )
            # A crash after Telegram send but before Gateway acknowledgment
            # leaves the same message_id already notified in Core. Treat that
            # idempotent duplicate as successful for Gateway delivery too.
            if result.notified or result.duplicate:
                store.mark_event_notified(event["message_id"])
            if result.telegram_message_id is not None:
                store.map_telegram_message(
                    result.telegram_message_id, event["agent_id"], event["task_id"], event["message_id"]
                )
            elif result.rejected_reason:
                store.mark_event_notified(event["message_id"], result.rejected_reason)
        except Exception as exc:  # Telegram is best effort; event remains durable for retry.
            store.mark_event_notified(event["message_id"], type(exc).__name__)
