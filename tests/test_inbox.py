from reporter import notifications, poller


def _seed_inbox_entry(store, settings, telegram, text="do the thing"):
    notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id="claude-1", agent_name="claude-1", task_id="task-a",
        status="OWNER_ACTION_REQUIRED", summary="need input", details=None, client_timestamp=None,
    )
    update = {
        "update_id": 1,
        "message": {"message_id": 6001, "chat": {"id": int(settings.chat_id)}, "text": text},
    }
    poller.process_update(store, settings, update)
    return store.inbox_for("claude-1")[0]["id"]


def test_acknowledge_marks_entry_as_consumed(client, store, settings, telegram):
    entry_id = _seed_inbox_entry(store, settings, telegram)
    r = client.get("/v1/agents/claude-1/inbox?unacknowledged_only=true")
    assert len(r.json()["inbox"]) == 1

    ack = client.post(f"/v1/agents/claude-1/inbox/{entry_id}/ack")
    assert ack.status_code == 200
    assert ack.json()["acknowledged"] is True

    r2 = client.get("/v1/agents/claude-1/inbox?unacknowledged_only=true")
    assert r2.json()["inbox"] == []


def test_acknowledging_twice_does_not_reprocess(client, store, settings, telegram):
    entry_id = _seed_inbox_entry(store, settings, telegram)
    first = client.post(f"/v1/agents/claude-1/inbox/{entry_id}/ack")
    assert first.status_code == 200
    second = client.post(f"/v1/agents/claude-1/inbox/{entry_id}/ack")
    assert second.status_code == 404  # already acknowledged -- cannot double-consume


def test_acknowledging_unknown_entry_id_is_rejected(client, store, settings, telegram):
    _seed_inbox_entry(store, settings, telegram)
    r = client.post("/v1/agents/claude-1/inbox/999999/ack")
    assert r.status_code == 404


def test_inbox_for_unknown_agent_id_is_rejected(client):
    r = client.get("/v1/agents/not-a-real-agent/inbox")
    assert r.status_code == 403


def test_status_reports_pending_owner_instruction_count(client, store, settings, telegram):
    _seed_inbox_entry(store, settings, telegram)
    r = client.get("/v1/status")
    agents = {a["agent_id"]: a for a in r.json()["agents"]}
    assert agents["claude-1"]["pending_owner_instructions"] == 1
