from __future__ import annotations

import hashlib
import hmac
import json
import time
import sqlite3

import pytest
from fastapi.testclient import TestClient

from gateway.api import create_app
from gateway.config import GatewaySettings
from gateway.notifier import ReporterCoreTelegramNotifier
from gateway.store import GatewayStore
from reporter.config import Settings
from reporter.store import Store
from tests.fakes import FakeTelegramClient


SECRET = "completion-test-secret"


def _headers(payload, *, secret=SECRET, path="/v1/agent-events"):
    body = json.dumps(payload, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    nonce = hashlib.sha256(f"{time.time_ns()}-{path}".encode()).hexdigest()
    raw = b"\n".join([timestamp.encode(), nonce.encode(), b"POST", path.encode(), body])
    return {"Content-Type": "application/json", "X-Reporter-Timestamp": timestamp,
            "X-Reporter-Nonce": nonce,
            "X-Reporter-Signature": hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()}


@pytest.fixture()
def direct(tmp_path):
    gateway_settings = GatewaySettings(db_path=tmp_path / "gateway.db", adapter_secret=SECRET,
                                       core_secret="core-secret")
    gateway = GatewayStore(gateway_settings.db_path)
    core = Store(tmp_path / "core.db")
    settings = Settings(bot_token="fake-token", chat_id="owner-chat",
                        allowed_agent_ids=frozenset({"codex-1", "claude-1"}))
    telegram = FakeTelegramClient()
    notifier = ReporterCoreTelegramNotifier(gateway, core_store=core, core_settings=settings,
                                             telegram=telegram)
    client = TestClient(create_app(store=gateway, settings=gateway_settings, notifier=notifier))
    return client, gateway, telegram


def _payload(status="PASS", **overrides):
    payload = {"provider": "claude", "agent_id": "claude-1", "task_id": "task-1",
               "message_id": "claude:task-1:terminal-1", "status": status,
               "summary": "task completed", "repository": "owner/repo",
               "branch": "feature/task", "source": "cloud-self-report"}
    payload.update(overrides)
    return payload


@pytest.mark.parametrize("status", ["PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "CANCELLED", "TIMED_OUT"])
def test_each_terminal_completion_notifies_once(direct, status):
    client, _, telegram = direct
    payload = _payload(status, message_id=f"claude:task-1:{status}")
    response = client.post("/v1/agent-events", json=payload, headers=_headers(payload))
    assert response.status_code == 200
    assert response.json()["accepted"] is True
    assert len(telegram.sent) == 1
    assert status in telegram.sent[0].text
    assert "Provider: claude" in telegram.sent[0].text
    assert "Repo: owner/repo" in telegram.sent[0].text


def test_explicit_message_id_replay_is_deduplicated(direct):
    client, gateway, telegram = direct
    payload = _payload()
    assert client.post("/v1/agent-events", json=payload, headers=_headers(payload)).status_code == 200
    replay = client.post("/v1/agent-events", json=payload, headers=_headers(payload))
    assert replay.status_code == 200 and replay.json()["duplicate"] is True
    assert len(telegram.sent) == 1
    with sqlite3.connect(gateway.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_distinct_terminal_identity_for_same_task_notifies_again(direct):
    client, _, telegram = direct
    first = _payload(message_id="claude:task-1:attempt-1")
    second = _payload(message_id="claude:task-1:attempt-2", status="FAILED", summary="retry failed")
    client.post("/v1/agent-events", json=first, headers=_headers(first))
    client.post("/v1/agent-events", json=second, headers=_headers(second))
    assert len(telegram.sent) == 2


def test_missing_message_id_gets_deterministic_fallback(direct):
    client, gateway, telegram = direct
    payload = _payload(message_id=None, completed_at="2026-09-07T00:00:00Z")
    response = client.post("/v1/agent-events", json=payload, headers=_headers(payload))
    assert response.status_code == 200
    message_id = response.json()["message_id"]
    assert message_id.startswith("cloud-")
    reopened = GatewayStore(gateway.db_path)
    assert reopened.telegram_message_target(1000)["task_id"] == "task-1"
    assert len(telegram.sent) == 1


@pytest.mark.parametrize("field", ["summary", "details"])
def test_secret_content_is_rejected_before_gateway_persistence(direct, field):
    client, gateway, _ = direct
    payload = _payload(**{field: "Authorization: Bearer abcdefghij1234567890"})
    response = client.post("/v1/agent-events", json=payload, headers=_headers(payload))
    assert response.status_code == 422
    with sqlite3.connect(gateway.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_direct_endpoint_fails_closed_for_auth_status_and_size(direct):
    client, _, _ = direct
    payload = _payload()
    assert client.post("/v1/agent-events", json=payload).status_code == 401
    assert client.post("/v1/agent-events", json=payload, headers=_headers(payload, secret="wrong")).status_code == 401
    invalid = _payload(status="NOT_TERMINAL")
    assert client.post("/v1/agent-events", json=invalid, headers=_headers(invalid)).status_code == 422
    oversized = _payload(details="x" * 70000)
    assert client.post("/v1/agent-events", json=oversized, headers=_headers(oversized)).status_code == 413


def test_correlation_fields_survive_restart(direct):
    client, gateway, _ = direct
    payload = _payload(provider="codex", agent_id="codex-1", repository="a/b", branch="main",
                       commit_sha="abc123", started_at="2026-09-06T00:00:00Z",
                       completed_at="2026-09-07T00:00:00Z")
    assert client.post("/v1/agent-events", json=payload, headers=_headers(payload)).status_code == 200
    reopened = GatewayStore(gateway.db_path)
    with reopened._connect() as conn:
        row = dict(conn.execute("SELECT * FROM events WHERE message_id = ?",
                                (payload["message_id"],)).fetchone())
    assert {k: row[k] for k in ("provider", "repository", "branch", "commit_sha", "started_at", "completed_at")} == {
        "provider": "codex", "repository": "a/b", "branch": "main", "commit_sha": "abc123",
        "started_at": "2026-09-06T00:00:00Z", "completed_at": "2026-09-07T00:00:00Z"}
