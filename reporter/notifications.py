"""Ties together: a pre-persistence secret guard (raw summary/details are
screened before anything reaches disk), event recording (store), dedup,
formatting, and the outbound secret guard, then (if not a duplicate and the
status is one that notifies) sends via the injected TelegramClient and
records the message_id -> agent/task mapping used later for reply routing.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Optional

from . import formatting, security
from .config import Settings
from .store import Store
from .telegram import TelegramClient

# CANCELLED/TIMED_OUT are terminal outcomes worth an Owner notification, same
# as PASS/FAILED. QUEUED/WAITING are transient, expected, non-actionable
# states (e.g. a cloud agent task sitting in a queue, or waiting on routine
# CI) -- they are recorded (visible in /v1/status) but deliberately never
# notify, so a busy queue or a normal CI wait never becomes Telegram spam.
NOTIFYING_STATUSES = {"PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "CANCELLED", "TIMED_OUT"}
ALL_STATUSES = NOTIFYING_STATUSES | {"RUNNING", "IDLE", "QUEUED", "WAITING"}


@dataclass
class EventResult:
    event_id: int
    notified: bool
    duplicate: bool
    message_id: str = ""
    telegram_message_id: Optional[int] = None
    rejected_reason: Optional[str] = None


def handle_event(*, store: Store, settings: Settings, telegram: Optional[TelegramClient],
                 agent_id: str, agent_name: str, task_id: str, status: str, summary: str,
                 details: Optional[str], client_timestamp: Optional[str],
                 message_id: Optional[str] = None) -> EventResult:
    """message_id is the canonical event-identity contract: optional input,
    preserved exactly when a trusted producer (e.g. the gateway/bridge)
    supplies one, and generated here when absent so every event -- including
    ones from the plain local CLI, which never sends one -- has a stable id
    for dedup, idempotency, and cross-system correlation."""
    resolved_message_id = message_id or uuid.uuid4().hex

    # Pre-persistence secret guard: screen the RAW summary/details before
    # anything is ever written to disk. This runs for every event regardless
    # of status -- a status that never notifies (e.g. RUNNING) would
    # otherwise persist a raw secret-shaped value indefinitely with no
    # outbound check ever running against it. Only safe placeholder text is
    # persisted when blocked; the real content is never stored anywhere.
    block_reason = security.find_secret_like_pattern(summary)
    if block_reason is None and details:
        block_reason = security.find_secret_like_pattern(details)
    if block_reason is not None:
        event_id, is_duplicate = store.record_event(
            agent_id=agent_id, agent_name=agent_name, task_id=task_id, status=status,
            summary="[blocked before persistence: secret-shaped content]", details=None,
            client_timestamp=client_timestamp, message_id=resolved_message_id,
            content_blocked=True, block_reason=block_reason,
        )
        return EventResult(event_id=event_id, notified=False, duplicate=is_duplicate,
                           message_id=resolved_message_id,
                           rejected_reason="secret_like_content_blocked_pre_persistence")

    event_id, is_duplicate = store.record_event(
        agent_id=agent_id, agent_name=agent_name, task_id=task_id, status=status,
        summary=summary, details=details, client_timestamp=client_timestamp,
        message_id=resolved_message_id,
    )

    if status not in NOTIFYING_STATUSES:
        return EventResult(event_id=event_id, notified=False, duplicate=is_duplicate,
                           message_id=resolved_message_id)
    if is_duplicate:
        return EventResult(event_id=event_id, notified=False, duplicate=True,
                           message_id=resolved_message_id)
    if telegram is None or not settings.telegram_configured():
        # Recorded, but genuinely cannot be delivered -- never silently
        # pretend this succeeded.
        return EventResult(event_id=event_id, notified=False, duplicate=False,
                           message_id=resolved_message_id, rejected_reason="telegram_not_configured")

    text = formatting.format_notification(
        agent_name=agent_name, agent_id=agent_id, task_id=task_id, status=status,
        summary=summary, details=details,
    )
    try:
        sanitized = security.prepare_outbound_text(text, settings.max_telegram_message_len)
    except security.OutboundMessageRejected as exc:
        return EventResult(event_id=event_id, notified=False, duplicate=False,
                           message_id=resolved_message_id, rejected_reason=str(exc))

    telegram_message_id = telegram.send_message(settings.chat_id, sanitized.text)
    store.map_telegram_message(telegram_message_id, agent_id, task_id)
    store.mark_notified(event_id)
    return EventResult(event_id=event_id, notified=True, duplicate=False,
                       message_id=resolved_message_id, telegram_message_id=telegram_message_id)
