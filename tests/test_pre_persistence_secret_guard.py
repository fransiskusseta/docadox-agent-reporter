"""Pre-persistence secret guard: raw secret-shaped summary/details must
never reach SQLite, even when outbound Telegram delivery would also (or
separately) block them. Only safe placeholder text + audit metadata
(content_blocked, block_reason) is ever persisted for such an event.
"""
import sqlite3

from reporter import notifications

SECRET_SUMMARY = "Configured. Authorization: Bearer abcdefghij1234567890"
SECRET_DETAILS = "api_key=sk-verylongsecretvaluehere1234"


def _send(store, settings, telegram, status="PASS", summary="ordinary summary",
          details=None, task_id="task-a", message_id=None):
    return notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="Claude #1", task_id=task_id,
        status=status, summary=summary, details=details, client_timestamp=None,
        message_id=message_id,
    )


def _raw_row(store, event_id):
    with sqlite3.connect(store.db_path) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def test_secret_shaped_summary_is_blocked_and_raw_text_absent_from_db(store, settings, telegram):
    result = _send(store, settings, telegram, summary=SECRET_SUMMARY)
    assert result.notified is False
    assert result.rejected_reason == "secret_like_content_blocked_pre_persistence"
    assert telegram.sent == []

    row = _raw_row(store, result.event_id)
    assert "Bearer" not in row["summary"]
    assert "abcdefghij1234567890" not in row["summary"]


def test_secret_shaped_details_is_blocked_and_raw_text_absent_from_db(store, settings, telegram):
    result = _send(store, settings, telegram, summary="ordinary summary", details=SECRET_DETAILS)
    assert result.notified is False
    assert result.rejected_reason == "secret_like_content_blocked_pre_persistence"

    row = _raw_row(store, result.event_id)
    assert row["details"] is None
    assert "sk-verylongsecretvaluehere1234" not in (row["summary"] or "")
    assert "sk-verylongsecretvaluehere1234" not in (row["details"] or "")


def test_blocked_event_retains_safe_audit_metadata_only(store, settings, telegram):
    result = _send(store, settings, telegram, summary=SECRET_SUMMARY, task_id="task-audit",
                   message_id="msg-audit-1")
    row = _raw_row(store, result.event_id)
    assert row["agent_id"] == "claude-1"
    assert row["task_id"] == "task-audit"
    assert row["status"] == "PASS"
    assert row["message_id"] == "msg-audit-1"
    assert row["content_blocked"] == 1
    assert row["block_reason"]  # a non-empty reason code (a pattern label, not the secret value)
    assert "abcdefghij1234567890" not in row["block_reason"]


def test_ordinary_event_still_persists_and_notifies(store, settings, telegram):
    result = _send(store, settings, telegram, summary="Everything is fine")
    assert result.notified is True
    assert len(telegram.sent) == 1
    row = _raw_row(store, result.event_id)
    assert row["summary"] == "Everything is fine"
    assert row["content_blocked"] == 0
    assert row["block_reason"] is None


def test_duplicate_idempotency_behavior_unchanged_for_non_secret_events(store, settings, telegram):
    first = _send(store, settings, telegram, summary="Same summary text")
    second = _send(store, settings, telegram, summary="Same summary text")
    assert first.notified is True
    assert second.duplicate is True
    assert len(telegram.sent) == 1

    # message_id idempotency also unaffected.
    third = _send(store, settings, telegram, summary="Distinct one", message_id="msg-idem-1")
    fourth = _send(store, settings, telegram, summary="Distinct one", message_id="msg-idem-1")
    assert third.event_id == fourth.event_id
    assert fourth.duplicate is True
    assert len(telegram.sent) == 2  # not sent a third time


def test_gateway_originated_secret_shaped_event_follows_same_safe_path(client, telegram):
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-gw", "status": "PASS",
        "summary": SECRET_SUMMARY, "message_id": "bridge-secret-event-1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["notified"] is False
    assert body["rejected_reason"] == "secret_like_content_blocked_pre_persistence"
    assert body["message_id"] == "bridge-secret-event-1"
    assert telegram.sent == []
