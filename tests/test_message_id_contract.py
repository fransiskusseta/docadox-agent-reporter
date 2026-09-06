"""Canonical event-identity contract: message_id is optional input on
POST /v1/events / notifications.handle_event. Absent -> Reporter Core
generates one. Supplied (e.g. by the gateway/bridge) -> preserved exactly
and used for dedup/idempotency/correlation. Existing local CLI producers,
which never send message_id, must keep working unchanged.
"""
from reporter import notifications


def _send(store, settings, telegram, status="PASS", summary="Test summary",
          task_id="task-a", message_id=None):
    return notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="Claude #1", task_id=task_id,
        status=status, summary=summary, details=None, client_timestamp=None,
        message_id=message_id,
    )


def test_missing_message_id_is_generated(store, settings, telegram):
    result = _send(store, settings, telegram)
    assert result.message_id  # non-empty
    assert isinstance(result.message_id, str)


def test_supplied_message_id_is_preserved_exactly(store, settings, telegram):
    result = _send(store, settings, telegram, message_id="gateway-event-abc123")
    assert result.message_id == "gateway-event-abc123"


def test_duplicate_supplied_message_id_is_deduplicated_safely(store, settings, telegram):
    first = _send(store, settings, telegram, message_id="gateway-event-dup-1")
    second = _send(store, settings, telegram, message_id="gateway-event-dup-1")
    assert first.notified is True
    assert second.notified is False
    assert second.duplicate is True
    assert second.event_id == first.event_id  # no second row inserted
    assert len(telegram.sent) == 1  # never sent twice


def test_distinct_supplied_message_ids_allow_distinct_same_content_events(store, settings, telegram):
    close_one = _send(store, settings, telegram, status="CANCELLED", summary="PR closed", task_id="pr:7",
                      message_id="github-delivery-close-1")
    # A reopen transition occurs between these close events in production;
    # the two delivery-qualified IDs must remain distinct even though the
    # normalized close content is identical.
    close_two = _send(store, settings, telegram, status="CANCELLED", summary="PR closed", task_id="pr:7",
                      message_id="github-delivery-close-2")
    assert close_one.notified is True
    assert close_two.notified is True
    assert close_one.message_id != close_two.message_id
    assert len(telegram.sent) == 2


def test_retry_of_a_never_notified_message_id_is_not_treated_as_a_duplicate(store, settings, telegram):
    # telegram not configured on this settings instance -> first attempt is
    # recorded but genuinely never delivered; a retry with the same
    # message_id must still be allowed to succeed once delivery is possible.
    from reporter.config import Settings
    unconfigured = Settings(bot_token="", chat_id="", allowed_agent_ids=settings.allowed_agent_ids)

    first = _send(store, unconfigured, None, message_id="gateway-event-retry-1")
    assert first.notified is False
    assert first.rejected_reason == "telegram_not_configured"

    second = _send(store, settings, telegram, message_id="gateway-event-retry-1")
    assert second.notified is True
    assert second.event_id == first.event_id
    assert len(telegram.sent) == 1


def test_existing_local_cli_producer_still_works_without_message_id(store, settings, telegram):
    # Exactly the existing content-hash dedup contract, unaffected by the
    # message_id addition.
    first = _send(store, settings, telegram, summary="Same summary text")
    second = _send(store, settings, telegram, summary="Same summary text")
    assert first.notified is True
    assert second.duplicate is True
    assert first.message_id != second.message_id  # each generated independently
    assert len(telegram.sent) == 1


def test_gateway_shaped_event_is_accepted_via_the_http_api(client, telegram):
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
        "message_id": "bridge-forwarded-event-1",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["notified"] is True
    assert body["message_id"] == "bridge-forwarded-event-1"

    # Resubmitting the same gateway-supplied message_id must not double-send.
    r2 = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
        "message_id": "bridge-forwarded-event-1",
    })
    assert r2.status_code == 200
    assert r2.json()["duplicate"] is True
    assert len(telegram.sent) == 1


def test_unrelated_validation_is_unchanged(client):
    # extra: forbid and status enum validation must still reject exactly as
    # before -- the message_id addition must not weaken either.
    r = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "PASS", "summary": "hi",
        "totally_unexpected_field": "nope",
    })
    assert r.status_code == 422

    r2 = client.post("/v1/events", json={
        "agent_id": "claude-1", "task_id": "task-x", "status": "NOT_A_REAL_STATUS", "summary": "hi",
    })
    assert r2.status_code == 422
