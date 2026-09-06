"""Webhook signature verification (test scenarios 9-10) and the separate
Copilot Cloud HTTP hook shared-secret path."""
import hashlib
import hmac
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from adapters.github.adapter import GitHubCloudAdapter, WebhookSignatureInvalid
from adapters.github.config import GitHubAdapterSettings
from adapters.github.models import PASS, RUNNING
from adapters.github.state import GitHubAdapterState
from adapters.github.webhook import verify_signature
from adapters.github.copilot_hooks import verify_hook_secret
from tests.github_fakes import FakeGitHubClient

WEBHOOK_SECRET = "test-webhook-secret-not-real"
COPILOT_HOOK_SECRET = "test-hook-secret-not-real"


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _adapter(tmp_path, monkeypatch, sink_calls: list):
    settings = GitHubAdapterSettings(
        token="fake-token", webhook_secret=WEBHOOK_SECRET, copilot_hook_secret=COPILOT_HOOK_SECRET,
        repos=("acme/widgets",), agent_id="copilot-1", state_db_path=tmp_path / "state.db",
    )
    adapter = GitHubCloudAdapter(settings, client=FakeGitHubClient(), state=GitHubAdapterState(settings.state_db_path))
    monkeypatch.setattr(adapter, "_send_to_local", lambda event: sink_calls.append(event) or {"ok": True})
    return adapter


# ── scenario 9: invalid signature rejected ──────────────────────────────────
def test_invalid_webhook_signature_is_rejected(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    body = json.dumps({"action": "opened", "number": 1, "pull_request": {"number": 1, "merged": False},
                       "repository": {"full_name": "acme/widgets"}}).encode()
    with pytest.raises(WebhookSignatureInvalid):
        adapter.receive_webhook(event_type="pull_request", signature_header="sha256=" + "0" * 64,
                                raw_body=body, delivery_id="d1")
    assert sink_calls == []  # never reached the sink


def test_missing_webhook_signature_is_rejected(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    body = b'{"action":"opened"}'
    with pytest.raises(WebhookSignatureInvalid):
        adapter.receive_webhook(event_type="pull_request", signature_header=None, raw_body=body, delivery_id="d2")


# ── scenario 10: valid webhook normalized correctly ─────────────────────────
def test_valid_webhook_is_normalized_and_reported(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    payload = {"action": "opened", "number": 5, "pull_request": {"number": 5, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    body = json.dumps(payload).encode()
    sig = _sign(WEBHOOK_SECRET, body)

    result = adapter.receive_webhook(event_type="pull_request", signature_header=sig, raw_body=body,
                                     delivery_id="d3")

    assert result == {"ok": True}
    assert len(sink_calls) == 1
    assert sink_calls[0].status == RUNNING
    assert sink_calls[0].agent_id == "copilot-1"
    assert sink_calls[0].pr_number == 5


def test_duplicate_webhook_delivery_id_is_not_reported_twice(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    payload = {"action": "opened", "number": 5, "pull_request": {"number": 5, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    body = json.dumps(payload).encode()
    sig = _sign(WEBHOOK_SECRET, body)

    adapter.receive_webhook(event_type="pull_request", signature_header=sig, raw_body=body, delivery_id="same-id")
    result2 = adapter.receive_webhook(event_type="pull_request", signature_header=sig, raw_body=body,
                                      delivery_id="same-id")

    assert result2 is None
    assert len(sink_calls) == 1  # only the first delivery was ever reported


def test_unsupported_webhook_event_ignored_without_signature_bypass():
    from adapters.github.webhook import parse_webhook_event
    assert parse_webhook_event("star", {"action": "created"}) is None


# ── Copilot Cloud HTTP hook: separate, shared-secret auth model ─────────────
def test_copilot_hook_invalid_secret_is_rejected(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    with pytest.raises(WebhookSignatureInvalid):
        adapter.receive_copilot_hook(event_name="sessionStart", secret_header="wrong-secret",
                                     payload={"sessionId": "s1"}, delivery_id="h1")
    assert sink_calls == []


def test_copilot_hook_missing_secret_configured_never_accepts_anything():
    # An adapter with NO copilot_hook_secret configured at all must reject
    # every hook, never treat "no secret configured" as "any request valid".
    assert verify_hook_secret("", "anything") is False
    assert verify_hook_secret("configured", None) is False


def test_copilot_hook_valid_secret_session_start_normalized(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    result = adapter.receive_copilot_hook(
        event_name="sessionStart", secret_header=COPILOT_HOOK_SECRET,
        payload={"sessionId": "s1", "initialPrompt": "fix the bug"}, delivery_id="h2",
    )
    assert result == {"ok": True}
    assert sink_calls[0].status == RUNNING
    assert "s1" in sink_calls[0].task_id


def test_copilot_hook_tool_level_events_are_not_translated(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    result = adapter.receive_copilot_hook(
        event_name="preToolUse", secret_header=COPILOT_HOOK_SECRET,
        payload={"sessionId": "s1", "toolName": "bash"}, delivery_id="h3",
    )
    assert result is None
    assert sink_calls == []


def test_copilot_hook_session_end_defaults_to_pass(tmp_path, monkeypatch):
    sink_calls: list = []
    adapter = _adapter(tmp_path, monkeypatch, sink_calls)
    adapter.receive_copilot_hook(event_name="SessionEnd", secret_header=COPILOT_HOOK_SECRET,
                                 payload={"sessionId": "s1"}, delivery_id="h4")
    assert sink_calls[0].status == PASS
