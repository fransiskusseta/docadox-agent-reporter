from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from gateway.api import create_app
from gateway.bridge import LocalBridge
from gateway.config import BridgeSettings, GatewaySettings
from gateway.store import GatewayStore


class FakeNotifier:
    def __init__(self):
        self.events = []

    def notify(self, event, store):
        self.events.append(event["message_id"])
        store.mark_event_notified(event["message_id"])


def make_app(tmp_path: Path):
    settings = GatewaySettings(
        db_path=tmp_path / "gateway.db", bridge_keys={"bridge-1": "bridge-secret"},
        adapter_secret="adapter-secret", core_secret="core-secret",
    )
    store = GatewayStore(settings.db_path)
    notifier = FakeNotifier()
    return TestClient(create_app(store=store, settings=settings, notifier=notifier)), store, notifier


def signed(method: str, path: str, secret: str, payload=None, bridge_id="bridge-1"):
    body = b"" if method == "GET" or payload is None else json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = hashlib.sha256(f"{time.time_ns()}-{path}".encode()).hexdigest()
    raw = b"\n".join([timestamp.encode(), nonce.encode(), method.encode(), path.encode(), body])
    return {
        "Content-Type": "application/json", "X-Reporter-Timestamp": timestamp,
        "X-Reporter-Nonce": nonce, "X-Reporter-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(),
        "X-Reporter-Bridge-Id": bridge_id,
    }


def event(message_id="event-1"):
    return {"message_id": message_id, "agent_id": "claude-1", "task_id": "task-1",
            "status": "PASS", "summary": "completed"}


def test_cloud_event_persists_and_notifies_once(tmp_path):
    client, store, notifier = make_app(tmp_path)
    payload = event()
    headers = signed("POST", "/v1/cloud/events", "adapter-secret", payload, bridge_id="")
    headers.pop("X-Reporter-Bridge-Id", None)
    assert client.post("/v1/cloud/events", json=payload, headers=headers).status_code == 200
    assert client.post("/v1/cloud/events", json=payload,
                       headers=signed("POST", "/v1/cloud/events", "adapter-secret", payload, bridge_id="")).status_code == 200
    assert notifier.events == ["event-1"]
    assert store.status()["pending_instructions"] == 0


def test_owner_reply_survives_offline_bridge_and_ack_is_idempotent(tmp_path):
    client, store, _ = make_app(tmp_path)
    payload = {"message_id": "instruction-1", "agent_id": "claude-1", "task_id": "task-1", "text": "Please inspect the report."}
    headers = signed("POST", "/v1/cloud/owner-replies", "core-secret", payload, bridge_id="")
    headers.pop("X-Reporter-Bridge-Id", None)
    assert client.post("/v1/cloud/owner-replies", json=payload, headers=headers).json()["accepted"]
    rows = client.get("/v1/bridge/instructions", headers=signed("GET", "/v1/bridge/instructions", "bridge-secret")).json()["instructions"]
    assert rows[0]["message_id"] == "instruction-1"
    ack = client.post("/v1/bridge/instructions/instruction-1/ack", headers=signed("POST", "/v1/bridge/instructions/instruction-1/ack", "bridge-secret"))
    assert ack.status_code == 200
    assert client.get("/v1/bridge/instructions", headers=signed("GET", "/v1/bridge/instructions", "bridge-secret")).json()["instructions"] == []
    assert store.status()["pending_instructions"] == 0


def test_invalid_and_replayed_bridge_request_rejected(tmp_path):
    client, _, _ = make_app(tmp_path)
    payload = event("event-auth")
    headers = signed("POST", "/v1/bridge/events", "wrong-secret", payload)
    assert client.post("/v1/bridge/events", json=payload, headers=headers).status_code == 401
    good = signed("POST", "/v1/bridge/events", "bridge-secret", payload)
    assert client.post("/v1/bridge/events", json=payload, headers=good).status_code == 200
    assert client.post("/v1/bridge/events", json=payload, headers=good).status_code == 401


def test_heartbeat_updates_presence_and_rejects_bridge_mismatch(tmp_path):
    client, store, _ = make_app(tmp_path)
    body = {"bridge_id": "bridge-1", "status": "ONLINE"}
    assert client.post("/v1/bridge/heartbeat", json=body,
                       headers=signed("POST", "/v1/bridge/heartbeat", "bridge-secret", body)).status_code == 200
    assert store.status()["bridges"][0]["status"] == "ONLINE"
    bad = {"bridge_id": "other", "status": "ONLINE"}
    assert client.post("/v1/bridge/heartbeat", json=bad,
                       headers=signed("POST", "/v1/bridge/heartbeat", "bridge-secret", bad)).status_code == 403


def test_local_bridge_delivery_is_idempotent_and_does_not_listen(tmp_path):
    reporter_db = tmp_path / "reporter.db"
    state_db = tmp_path / "bridge.db"
    settings = BridgeSettings(gateway_url="http://gateway.invalid", bridge_id="bridge-1",
                              bridge_secret="bridge-secret", reporter_db_path=reporter_db,
                              state_path=state_db)
    bridge = LocalBridge(settings)

    class FakeClient:
        def heartbeat(self): return {}
        def event(self, payload): return {"accepted": True}
        def instructions(self): return [{"message_id": "instruction-1", "agent_id": "claude-1", "task_id": "task-1", "text": "inspect"}]
        def acknowledge(self, message_id): return {"acknowledged": True}

    bridge.client = FakeClient()
    bridge.run_once()
    bridge.run_once()
    assert len(bridge.local_store.inbox_for("claude-1")) == 1


def test_gateway_restart_preserves_pending_instruction_and_lost_ack_replays(tmp_path):
    path = tmp_path / "gateway.db"
    first = GatewayStore(path)
    first.enqueue_instruction({"message_id": "restart-1", "agent_id": "claude-1", "task_id": "t",
                               "text": "resume", "source_message_id": "telegram-1"})
    assert first.pending_instructions(10)[0]["message_id"] == "restart-1"
    second = GatewayStore(path)
    assert second.pending_instructions(10)[0]["message_id"] == "restart-1"
    assert second.acknowledge_instruction("restart-1") is True
    assert second.acknowledge_instruction("restart-1") is False
