from reporter import notifications


def _send(store, settings, telegram, status, summary="Test summary", task_id="task-a"):
    return notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="Claude #1", task_id=task_id,
        status=status, summary=summary, details=None, client_timestamp=None,
    )


def test_pass_triggers_immediate_notification(store, settings, telegram):
    result = _send(store, settings, telegram, "PASS")
    assert result.notified is True
    assert len(telegram.sent) == 1
    assert "PASS" in telegram.sent[0].text
    assert telegram.sent[0].chat_id == settings.chat_id


def test_blocked_triggers_immediate_notification(store, settings, telegram):
    result = _send(store, settings, telegram, "BLOCKED")
    assert result.notified is True
    assert len(telegram.sent) == 1
    assert "BLOCKED" in telegram.sent[0].text


def test_owner_action_required_triggers_immediate_notification(store, settings, telegram):
    result = _send(store, settings, telegram, "OWNER_ACTION_REQUIRED")
    assert result.notified is True
    assert len(telegram.sent) == 1
    assert "OWNER ACTION" in telegram.sent[0].text.upper() or "Owner action" in telegram.sent[0].text


def test_running_does_not_notify(store, settings, telegram):
    result = _send(store, settings, telegram, "RUNNING")
    assert result.notified is False
    assert telegram.sent == []


def test_idle_does_not_notify(store, settings, telegram):
    result = _send(store, settings, telegram, "IDLE")
    assert result.notified is False
    assert telegram.sent == []


def test_duplicate_event_is_deduplicated(store, settings, telegram):
    first = _send(store, settings, telegram, "PASS", summary="Same summary text")
    second = _send(store, settings, telegram, "PASS", summary="Same summary text")
    assert first.notified is True
    assert second.notified is False
    assert second.duplicate is True
    assert len(telegram.sent) == 1  # not sent twice


def test_same_status_different_summary_is_not_a_duplicate(store, settings, telegram):
    _send(store, settings, telegram, "PASS", summary="First summary")
    second = _send(store, settings, telegram, "PASS", summary="Second, different summary")
    assert second.duplicate is False
    assert len(telegram.sent) == 2


def test_obvious_secret_containing_message_is_blocked(store, settings, telegram):
    result = _send(store, settings, telegram, "PASS",
                   summary="Done. Authorization: Bearer abcdefghij1234567890")
    assert result.notified is False
    assert result.rejected_reason is not None
    assert telegram.sent == []  # never reaches Telegram


def test_api_key_assignment_is_blocked(store, settings, telegram):
    result = _send(store, settings, telegram, "PASS",
                   summary="Configured", task_id="task-b",
                   )
    # sanity: a normal message is NOT blocked
    assert result.notified is True
    blocked = notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="Claude #1", task_id="task-c",
        status="PASS", summary="api_key=sk-verylongsecretvaluehere1234", details=None,
        client_timestamp=None,
    )
    assert blocked.notified is False
    assert blocked.rejected_reason is not None


def test_event_is_always_recorded_even_when_not_notified(store, settings, telegram):
    _send(store, settings, telegram, "RUNNING")
    rows = store.latest_state_per_agent()
    assert len(rows) == 1
    assert rows[0]["status"] == "RUNNING"
