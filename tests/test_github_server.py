"""End-to-end HTTP-level tests for adapters/github/server.py (the standalone
webhook/hook receiver), using FastAPI's TestClient -- no real network."""
import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from adapters.github.adapter import GitHubCloudAdapter
from adapters.github.config import GitHubAdapterSettings
from adapters.github.server import create_app
from adapters.github.state import GitHubAdapterState
from tests.github_fakes import FakeGitHubClient

WEBHOOK_SECRET = "test-webhook-secret"
HOOK_SECRET = "test-hook-secret"


def _client(tmp_path, monkeypatch):
    settings = GitHubAdapterSettings(token="fake-token", webhook_secret=WEBHOOK_SECRET,
                                     copilot_hook_secret=HOOK_SECRET, repos=("acme/widgets",),
                                     agent_id="copilot-1", state_db_path=tmp_path / "state.db")
    adapter = GitHubCloudAdapter(settings, client=FakeGitHubClient(), state=GitHubAdapterState(settings.state_db_path))
    sent = []
    monkeypatch.setattr(adapter, "_send_to_local", lambda event: sent.append(event) or {"ok": True})
    app = create_app(adapter=adapter, settings=settings)
    return TestClient(app), sent


def _sign(body: bytes) -> str:
    return "sha256=" + hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_health_endpoint_never_returns_a_secret(tmp_path, monkeypatch):
    client, _ = _client(tmp_path, monkeypatch)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert WEBHOOK_SECRET not in json.dumps(body)
    assert HOOK_SECRET not in json.dumps(body)
    assert body["token_configured"] is True


def test_webhook_endpoint_rejects_bad_signature(tmp_path, monkeypatch):
    client, sent = _client(tmp_path, monkeypatch)
    payload = {"action": "opened", "number": 1, "pull_request": {"number": 1, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    r = client.post("/github/webhook", content=json.dumps(payload),
                    headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": "sha256=" + "0" * 64,
                            "X-GitHub-Delivery": "d1", "Content-Type": "application/json"})
    assert r.status_code == 401
    assert sent == []


def test_webhook_endpoint_accepts_valid_signature(tmp_path, monkeypatch):
    client, sent = _client(tmp_path, monkeypatch)
    payload = {"action": "opened", "number": 1, "pull_request": {"number": 1, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    body = json.dumps(payload).encode()
    r = client.post("/github/webhook", content=body,
                    headers={"X-GitHub-Event": "pull_request", "X-Hub-Signature-256": _sign(body),
                            "X-GitHub-Delivery": "d2", "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["processed"] is True
    assert len(sent) == 1


def test_webhook_endpoint_ignores_unsupported_event_type_without_processing(tmp_path, monkeypatch):
    client, sent = _client(tmp_path, monkeypatch)
    r = client.post("/github/webhook", content=b"{}",
                    headers={"X-GitHub-Event": "star", "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["ignored"] is True
    assert sent == []


def test_copilot_hook_endpoint_rejects_bad_secret(tmp_path, monkeypatch):
    client, sent = _client(tmp_path, monkeypatch)
    r = client.post("/github/copilot-hook", json={"sessionId": "s1"},
                    headers={"X-Copilot-Hook-Event": "sessionStart", "X-Reporter-Hook-Secret": "wrong"})
    assert r.status_code == 401
    assert sent == []


def test_copilot_hook_endpoint_accepts_valid_secret(tmp_path, monkeypatch):
    client, sent = _client(tmp_path, monkeypatch)
    r = client.post("/github/copilot-hook", json={"sessionId": "s1"},
                    headers={"X-Copilot-Hook-Event": "sessionStart", "X-Reporter-Hook-Secret": HOOK_SECRET})
    assert r.status_code == 200
    assert r.json()["processed"] is True
    assert len(sent) == 1
