"""Focused mock-only cross-component release harness.

This deliberately exercises the real normalized GitHub event, Gateway API,
Reporter Core notification/routing code, and Local Bridge delivery state. No
Telegram, GitHub, or cloud credentials are used.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from adapters.github.adapter import GitHubCloudAdapter, _message_id_for
from adapters.github.config import GitHubAdapterSettings
from adapters.github.state import GitHubAdapterState
from gateway.api import create_app
from gateway.bridge import LocalBridge
from gateway.bridge_state import BridgeState
from gateway.config import BridgeSettings, GatewaySettings
from gateway.notifier import ReporterCoreTelegramNotifier
from gateway.store import GatewayStore
from reporter.config import Settings
from reporter.poller import process_update
from reporter.store import Store
from tests.fakes import FakeTelegramClient
from tests.github_fakes import FakeGitHubClient


def _headers(method: str, path: str, secret: str, payload: dict | None = None, *, bridge_id: str | None = None):
    body = b"" if method == "GET" else json.dumps(payload or {}, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = hashlib.sha256(f"{time.time_ns()}-{path}".encode()).hexdigest()
    raw = b"\n".join([timestamp.encode(), nonce.encode(), method.encode(), path.encode(), body])
    result = {
        "Content-Type": "application/json",
        "X-Reporter-Timestamp": timestamp,
        "X-Reporter-Nonce": nonce,
        "X-Reporter-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
    }
    if bridge_id is not None:
        result["X-Reporter-Bridge-Id"] = bridge_id
    return result


def _row(store: GatewayStore, message_id: str) -> dict:
    with store._connect() as conn:
        row = conn.execute("SELECT * FROM events WHERE message_id = ?", (message_id,)).fetchone()
        return dict(row)


def _setup(tmp_path: Path):
    gateway_settings = GatewaySettings(
        db_path=tmp_path / "gateway.db", bridge_keys={"bridge-1": "bridge-secret"},
        adapter_secret="adapter-secret", core_secret="core-secret",
    )
    gateway_store = GatewayStore(gateway_settings.db_path)
    core_settings = Settings(
        bot_token="fake-token", chat_id="owner-chat",
        allowed_agent_ids=frozenset({"copilot-1"}),
    )
    core_store = Store(tmp_path / "cloud-reporter.db")
    telegram = FakeTelegramClient()
    notifier = ReporterCoreTelegramNotifier(
        gateway_settings, core_store=core_store, core_settings=core_settings, telegram=telegram,
    )
    client = TestClient(create_app(store=gateway_store, settings=gateway_settings, notifier=notifier))
    return client, gateway_store, core_store, core_settings, telegram, gateway_settings


def _adapter(tmp_path: Path, client: TestClient) -> GitHubCloudAdapter:
    fake_github = FakeGitHubClient()
    fake_github.set_tasks("acme", "widgets", [{
        "id": "task-waiting", "state": "waiting_for_user",
        "updated_at": "2026-01-01T00:00:00Z",
    }])
    settings = GitHubAdapterSettings(
        token="fake-token", repos=("acme/widgets",), agent_id="copilot-1",
        state_db_path=tmp_path / "github.db", sink="gateway",
        gateway_url="http://gateway.test", gateway_adapter_secret="adapter-secret",
    )
    adapter = GitHubCloudAdapter(settings, client=fake_github,
                                 state=GitHubAdapterState(settings.state_db_path))

    def send(event):
        payload = {
            "message_id": _message_id_for(event), "agent_id": event.agent_id,
            "agent_name": event.agent_name, "task_id": event.task_id,
            "status": event.status, "summary": event.summary,
        }
        response = client.post(
            "/v1/cloud/events", json=payload,
            headers=_headers("POST", "/v1/cloud/events", "adapter-secret" , payload),
        )
        response.raise_for_status()
        return response.json()

    adapter._send_to_gateway = send
    return adapter


def test_github_waiting_event_reaches_gateway_reporter_and_telegram_once(tmp_path):
    client, gateway_store, core_store, _, telegram, _ = _setup(tmp_path)
    adapter = _adapter(tmp_path, client)

    event = adapter.poll()[0]
    result = adapter.report(event)
    duplicate = adapter.report(event)

    assert event.status == "OWNER_ACTION_REQUIRED"
    assert result["message_id"] == duplicate["message_id"] == _message_id_for(event)
    assert _row(gateway_store, _message_id_for(event))["status"] == "OWNER_ACTION_REQUIRED"
    with core_store._connect() as conn:
        stored = conn.execute("SELECT * FROM events WHERE message_id = ?", (_message_id_for(event),)).fetchone()
    assert stored["status"] == "OWNER_ACTION_REQUIRED"
    assert stored["agent_id"] == event.agent_id and stored["task_id"] == event.task_id
    assert len(telegram.sent) == 1


def test_timed_out_is_preserved_through_gateway_without_lossy_mapping(tmp_path):
    client, gateway_store, _, _, _, _ = _setup(tmp_path)
    payload = {
        "message_id": "github-timed-out-1", "agent_id": "copilot-1", "task_id": "task-timeout",
        "status": "TIMED_OUT", "summary": "cloud task timed out",
    }
    response = client.post("/v1/cloud/events", json=payload,
                           headers=_headers("POST", "/v1/cloud/events", "adapter-secret", payload))
    assert response.status_code == 200
    assert _row(gateway_store, "github-timed-out-1")["status"] == "TIMED_OUT"


def test_gateway_accepts_and_persists_every_canonical_status(tmp_path):
    client, gateway_store, _, _, _, _ = _setup(tmp_path)
    statuses = (
        "IDLE", "QUEUED", "RUNNING", "WAITING", "OWNER_ACTION_REQUIRED",
        "PASS", "BLOCKED", "FAILED", "CANCELLED", "TIMED_OUT",
    )
    for index, status in enumerate(statuses):
        payload = {
            "message_id": f"all-statuses-{index}", "agent_id": "copilot-1",
            "task_id": f"task-{index}", "status": status, "summary": status,
        }
        response = client.post(
            "/v1/cloud/events", json=payload,
            headers=_headers("POST", "/v1/cloud/events", "adapter-secret", payload),
        )
        assert response.status_code == 200
        assert _row(gateway_store, payload["message_id"])["status"] == status


def test_owner_reply_routes_from_telegram_to_exact_local_inbox_and_ack(tmp_path):
    client, gateway_store, cloud_store, core_settings, telegram, gateway_settings = _setup(tmp_path)
    event_payload = {
        "message_id": "cloud-owner-action-1", "agent_id": "copilot-1", "task_id": "task-77",
        "status": "OWNER_ACTION_REQUIRED", "summary": "need Owner input",
    }
    client.post("/v1/cloud/events", json=event_payload,
                headers=_headers("POST", "/v1/cloud/events", "adapter-secret", event_payload)).raise_for_status()
    # FakeTelegram's first outbound message id is 1000, and Reporter Core
    # persisted that id -> (agent_id, task_id) mapping.
    telegram_message_id = 1000
    process_update(cloud_store, core_settings, {
        "update_id": 1,
        "message": {
            "message_id": 1001, "chat": {"id": "owner-chat"}, "text": "Please investigate literally: $HOME",
            "reply_to_message": {"message_id": telegram_message_id},
        },
    })
    reply = cloud_store.inbox_for("copilot-1")[0]
    instruction = {
        "message_id": "owner-reply-1", "agent_id": reply["agent_id"], "task_id": reply["task_id"],
        "text": reply["message_text"], "source_message_id": "1001",
    }
    queued = client.post("/v1/cloud/owner-replies", json=instruction,
                         headers=_headers("POST", "/v1/cloud/owner-replies", "core-secret", instruction))
    assert queued.status_code == 200
    assert gateway_store.pending_instructions(10)[0]["text"] == "Please investigate literally: $HOME"

    local_store = Store(tmp_path / "local-reporter.db")
    bridge = LocalBridge(BridgeSettings(
        gateway_url="http://gateway.test", bridge_id="bridge-1", bridge_secret="bridge-secret",
        reporter_db_path=tmp_path / "local-reporter.db", state_path=tmp_path / "bridge.db",
    ))
    bridge.local_store = local_store

    class GatewayQueue:
        def heartbeat(self): return {}
        def event(self, event): return {}
        def instructions(self): return [dict(row) for row in gateway_store.pending_instructions(10)]
        def acknowledge(self, message_id):
            return {"acknowledged": gateway_store.acknowledge_instruction(message_id)}

    bridge.client = GatewayQueue()
    bridge.run_once()
    bridge.run_once()
    entries = local_store.inbox_for("copilot-1")
    assert len(entries) == 1
    assert entries[0]["task_id"] == "task-77"
    assert entries[0]["message_text"] == "Please investigate literally: $HOME"
    assert gateway_store.pending_instructions(10) == []


def test_owner_reply_stays_queued_while_bridge_offline_then_delivers(tmp_path):
    client, gateway_store, _, _, _, _ = _setup(tmp_path)
    instruction = {"message_id": "offline-1", "agent_id": "copilot-1", "task_id": "task-offline", "text": "resume"}
    client.post("/v1/cloud/owner-replies", json=instruction,
                headers=_headers("POST", "/v1/cloud/owner-replies", "core-secret", instruction)).raise_for_status()
    assert gateway_store.pending_instructions(10)[0]["message_id"] == "offline-1"

    local_store = Store(tmp_path / "local.db")
    bridge = LocalBridge(BridgeSettings(
        gateway_url="http://gateway.test", bridge_id="bridge-1", bridge_secret="bridge-secret",
        reporter_db_path=tmp_path / "local.db", state_path=tmp_path / "bridge.db",
    ))
    bridge.local_store = local_store

    class Reconnected:
        def heartbeat(self): return {}
        def event(self, event): return {}
        def instructions(self): return [dict(row) for row in gateway_store.pending_instructions(10)]
        def acknowledge(self, message_id):
            gateway_store.acknowledge_instruction(message_id)
            return {"acknowledged": True}

    bridge.client = Reconnected()
    bridge.run_once()
    assert len(local_store.inbox_for("copilot-1")) == 1
    assert gateway_store.pending_instructions(10) == []
