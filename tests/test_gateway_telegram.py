from __future__ import annotations

import sqlite3

from gateway.store import GatewayStore
from gateway.telegram_poller import CloudTelegramPoller
from reporter.config import Settings
from gateway.notifier import ReporterCoreTelegramNotifier
from reporter.store import Store
from tests.fakes import FakeTelegramClient


def _poller(tmp_path):
    gateway_store = GatewayStore(tmp_path / "gateway.db")
    settings = Settings(bot_token="fake-token-for-tests-only", chat_id="owner-chat", poll_interval_sec=0.01)
    telegram = FakeTelegramClient()
    poller = CloudTelegramPoller(gateway_store, settings, telegram)
    return gateway_store, settings, telegram, poller


def _reply(update_id=1, chat_id="owner-chat", reply_to=1000, text="Continue this task"):
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id + 1000,
            "chat": {"id": chat_id},
            "text": text,
            "reply_to_message": {"message_id": reply_to},
        },
    }


def test_outbound_cloud_mapping_resolves_exact_agent_task_and_message_id(tmp_path):
    gateway_store, core_settings, telegram, _ = _poller(tmp_path)
    core_store = Store(tmp_path / "core.db")
    notifier = ReporterCoreTelegramNotifier(
        gateway_store, core_store=core_store, core_settings=core_settings, telegram=telegram,
    )
    event = {"message_id": "cloud-event-1", "agent_id": "copilot-1", "task_id": "task-7",
             "status": "PASS", "summary": "completed"}
    notifier.notify(event, gateway_store)
    target = gateway_store.telegram_message_target(1000)
    assert dict(target) == {
        "telegram_message_id": 1000, "agent_id": "copilot-1", "task_id": "task-7",
        "source_message_id": "cloud-event-1", "created_at": target["created_at"],
    }


def test_known_cloud_reply_is_enqueued_once_and_duplicate_update_is_suppressed(tmp_path):
    gateway_store, _, _, poller = _poller(tmp_path)
    gateway_store.map_telegram_message(1000, "copilot-1", "task-7", "cloud-event-1")
    update = _reply()
    assert poller._consume(update) is None
    assert poller._consume(update) is None
    with sqlite3.connect(gateway_store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM instructions").fetchone()[0] == 1
    row = gateway_store.pending_instructions(10)[0]
    assert row["message_id"] == "telegram-reply:1"
    assert row["agent_id"] == "copilot-1"
    assert row["task_id"] == "task-7"
    assert row["source_message_id"] == "cloud-event-1"


def test_wrong_chat_and_uncorrelated_reply_are_consumed_without_queueing(tmp_path):
    gateway_store, _, _, poller = _poller(tmp_path)
    poller._consume(_reply(update_id=1, chat_id="other-chat"))
    poller._consume(_reply(update_id=2, reply_to=9999))
    assert gateway_store.pending_instructions(10) == []


def test_offset_and_update_dedup_survive_gateway_store_restart(tmp_path):
    gateway_store, _, _, poller = _poller(tmp_path)
    gateway_store.map_telegram_message(1000, "copilot-1", "task-7", "cloud-event-1")
    poller.telegram.queue_update(_reply(update_id=4))
    assert poller.poll_once() == 1
    assert gateway_store.telegram_offset() == 5
    reopened = GatewayStore(tmp_path / "gateway.db")
    assert reopened.telegram_offset() == 5
    assert reopened.consume_telegram_update(4) is False
    assert len(reopened.pending_instructions(10)) == 1


def test_secret_shaped_reply_is_not_persisted(tmp_path):
    gateway_store, _, _, poller = _poller(tmp_path)
    gateway_store.map_telegram_message(1000, "copilot-1", "task-7", "cloud-event-1")
    secret_text = "Authorization: Bearer abcdefghij1234567890"
    poller._consume(_reply(text=secret_text))
    with sqlite3.connect(gateway_store.db_path) as conn:
        rows = conn.execute("SELECT text FROM instructions").fetchall()
    assert rows == []
    assert secret_text not in gateway_store.db_path.read_bytes().decode("utf-8", errors="ignore")
