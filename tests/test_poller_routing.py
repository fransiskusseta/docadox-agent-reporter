from reporter import notifications, poller
from reporter.store import Store


def _send_owner_action_required(store, settings, telegram, agent_id="claude-1", task_id="task-a"):
    return notifications.handle_event(
        store=store, settings=settings, telegram=telegram,
        agent_id=agent_id, agent_name=agent_id, task_id=task_id,
        status="OWNER_ACTION_REQUIRED", summary="Need a decision", details=None,
        client_timestamp=None,
    )


def test_reply_routes_to_correct_agent_and_task_via_reply_to_message(store, settings, telegram):
    result = _send_owner_action_required(store, settings, telegram, agent_id="claude-1", task_id="task-a")
    # The reporter's own outbound message id (returned from handle_event) is
    # exactly what a real Telegram reply's `reply_to_message.message_id`
    # would reference when the Owner taps "Reply" on that notification.
    outbound_id = result.telegram_message_id
    assert outbound_id is not None

    update = {
        "update_id": 1,
        "message": {
            "message_id": 5001,
            "chat": {"id": int(settings.chat_id)},
            "text": "APPROVE DEPLOY EMERGENT-MVP-001",
            "reply_to_message": {"message_id": outbound_id},
        },
    }
    poller.process_update(store, settings, update)

    inbox = store.inbox_for("claude-1")
    assert len(inbox) == 1
    assert inbox[0]["task_id"] == "task-a"
    assert inbox[0]["message_text"] == "APPROVE DEPLOY EMERGENT-MVP-001"
    assert inbox[0]["contains_privileged_keyword"] == 1


def test_reply_from_wrong_chat_id_is_rejected(store, settings, telegram):
    _send_owner_action_required(store, settings, telegram)
    update = {
        "update_id": 1,
        "message": {
            "message_id": 5002,
            "chat": {"id": 111222333},  # NOT settings.chat_id
            "text": "this should be ignored",
        },
    }
    poller.process_update(store, settings, update)
    assert store.inbox_for("claude-1") == []


def test_attachment_only_message_is_never_processed(store, settings, telegram):
    _send_owner_action_required(store, settings, telegram)
    update = {
        "update_id": 1,
        "message": {
            "message_id": 5003,
            "chat": {"id": int(settings.chat_id)},
            "photo": [{"file_id": "abc"}],  # no "text" key at all
        },
    }
    poller.process_update(store, settings, update)
    assert store.inbox_for("claude-1") == []


def test_reply_falls_back_to_sole_pending_owner_action_when_not_a_reply(store, settings, telegram):
    _send_owner_action_required(store, settings, telegram, agent_id="claude-1", task_id="task-a")
    update = {
        "update_id": 1,
        "message": {
            "message_id": 5004,
            "chat": {"id": int(settings.chat_id)},
            "text": "ok proceed",  # plain message, not a Telegram "reply to"
        },
    }
    poller.process_update(store, settings, update)
    inbox = store.inbox_for("claude-1")
    assert len(inbox) == 1
    assert inbox[0]["message_text"] == "ok proceed"


def test_ambiguous_reply_with_two_pending_agents_is_marked_unrouted(store, settings, telegram):
    _send_owner_action_required(store, settings, telegram, agent_id="claude-1", task_id="task-a")
    _send_owner_action_required(store, settings, telegram, agent_id="claude-2", task_id="task-b")
    update = {
        "update_id": 1,
        "message": {
            "message_id": 5005,
            "chat": {"id": int(settings.chat_id)},
            "text": "ok proceed",
        },
    }
    poller.process_update(store, settings, update)
    assert store.inbox_for("claude-1") == []
    assert store.inbox_for("claude-2") == []
    unrouted = store.inbox_for("unrouted")
    assert len(unrouted) == 1
    assert unrouted[0]["routed"] == 0


def test_offset_persists_across_a_fresh_store_instance_preventing_replay(store, settings, telegram, tmp_path):
    _send_owner_action_required(store, settings, telegram)
    update = {
        "update_id": 42,
        "message": {
            "message_id": 5006,
            "chat": {"id": int(settings.chat_id)},
            "text": "noted",
        },
    }
    telegram.queue_update(update)
    processed = poller.poll_once(store, settings, telegram)
    assert processed == 1
    assert store.get_offset() == 43

    # Simulate a full process restart: a brand-new Store pointed at the
    # SAME on-disk database file must see the already-persisted offset.
    reopened = Store(store.db_path)
    assert reopened.get_offset() == 43
