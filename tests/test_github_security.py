"""No-secret-logging (test scenario 14), genuine Gateway-sink HMAC interop
with gateway/auth.py's own verification math, and the "ordinary PR review
waiting is not OWNER_ACTION_REQUIRED" distinction (this task's own item 7)."""
import hashlib
import hmac
import io
import sys
import time
from pathlib import Path
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from adapters.github.client import GitHubApiError, GitHubClient
from adapters.github.models import CHECK_CONCLUSION_MAP, OWNER_ACTION_REQUIRED
from adapters.github.webhook import parse_webhook_event

REAL_LOOKING_TOKEN = "github_pat_11ABCDEFG_ThisLooksLikeARealFineGrainedTokenValueXYZ987654321"


def test_no_token_appears_in_a_raised_api_error_message(monkeypatch):
    def _fake_urlopen(req, timeout=None):
        # The token IS present in the outgoing request header (that's where
        # it belongs) -- the assertion is that it never leaks back OUT into
        # anything this client raises or returns.
        assert req.get_header("Authorization") == f"Bearer {REAL_LOOKING_TOKEN}"
        raise HTTPError(req.full_url, 401, "Bad credentials", hdrs=None,
                        fp=io.BytesIO(b'{"message": "Bad credentials"}'))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    client = GitHubClient(token=REAL_LOOKING_TOKEN)
    with pytest.raises(GitHubApiError) as excinfo:
        client.list_tasks("acme", "widgets")

    assert REAL_LOOKING_TOKEN not in str(excinfo.value)


def test_no_token_appears_in_logged_output(monkeypatch, caplog):
    def _fake_urlopen(req, timeout=None):
        raise HTTPError(req.full_url, 500, "Internal Server Error", hdrs=None, fp=io.BytesIO(b"{}"))

    import urllib.request
    monkeypatch.setattr(urllib.request, "urlopen", _fake_urlopen)

    client = GitHubClient(token=REAL_LOOKING_TOKEN)
    with caplog.at_level("DEBUG"):
        try:
            client.get_task("acme", "widgets", "t1")
        except GitHubApiError:
            pass

    for record in caplog.records:
        assert REAL_LOOKING_TOKEN not in record.getMessage()


def test_no_source_file_logs_or_prints_the_whole_settings_object():
    """GitHubAdapterSettings is a plain dataclass -- its default __repr__
    DOES include every field value (token/secrets included), so the actual
    safety property this adapter relies on is that nothing ever passes a
    whole `settings` object to print()/logger.*()/an f-string. This scans
    every shipped source file for that specific mistake, rather than
    asserting something about the dataclass itself (which cannot be made
    safe by construction without diverging from the rest of this codebase's
    plain-dataclass Settings convention -- see reporter/config.py, gateway/
    config.py, both the same shape)."""
    import re

    package_dir = Path(__file__).resolve().parent.parent / "adapters" / "github"
    offending: list[str] = []
    pattern = re.compile(r"(print|logger\.\w+|logging\.\w+)\([^)]*\bsettings\b[^)]*\)")
    for py_file in package_dir.glob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        for match in pattern.finditer(text):
            # Allow settings.<safe_method>(...) calls (e.g. settings.token_configured()) --
            # only flag the bare `settings` object itself being interpolated.
            if re.search(r"\bsettings\.\w+", match.group(0)):
                continue
            offending.append(f"{py_file.name}: {match.group(0)!r}")
    assert offending == [], f"Found code that may log/print the whole settings object: {offending}"


# ── genuine interop: this adapter's Gateway signature matches gateway/auth.py ──
def test_gateway_sink_signature_matches_gateways_own_verification_math():
    from adapters.github.adapter import _gateway_signature

    secret = "shared-adapter-secret"
    timestamp = str(int(time.time()))
    nonce = "a" * 32
    method, path = "POST", "/v1/cloud/events"
    body = b'{"message_id":"abc","agent_id":"copilot-1"}'

    produced = _gateway_signature(secret, timestamp, nonce, method, path, body)

    # Reproduces gateway/auth.py's RequestAuthenticator.verify() computation
    # byte-for-byte (independently, not by importing it) to prove interop
    # without hard-coupling this test to that actively-developed module.
    expected = hmac.new(
        secret.encode(), b"\n".join([timestamp.encode(), nonce.encode(), method.encode(), path.encode(), body]),
        hashlib.sha256,
    ).hexdigest()
    assert produced == expected
    assert len(produced) == 64  # gateway/auth.py requires len(signature) == 64


# ── item 7: ordinary PR review waiting is NOT OWNER_ACTION_REQUIRED ────────
def test_ordinary_review_requested_is_not_owner_action_required():
    # review_requested is deliberately UNMAPPED (parse_webhook_event returns
    # None -- "no status claim", not a guess) precisely because an ordinary
    # PR awaiting review is not, on its own, a genuine "Owner must act now"
    # signal -- see PULL_REQUEST_ACTION_MAP's own docstring. None trivially
    # satisfies "never OWNER_ACTION_REQUIRED"; the real, positive assertion
    # of what DOES mean OWNER_ACTION_REQUIRED is the two tests below.
    payload = {"action": "review_requested", "number": 3,
              "pull_request": {"number": 3, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    event = parse_webhook_event("pull_request", payload)
    assert event is None or event.status != OWNER_ACTION_REQUIRED


def test_check_run_action_required_conclusion_is_owner_action_required():
    """GitHub's own "action_required" conclusion is the genuine, GitHub-
    asserted signal this maps to OWNER_ACTION_REQUIRED -- contrast with the
    test above, where an ordinary review request is deliberately NOT this
    status."""
    assert CHECK_CONCLUSION_MAP["action_required"] == OWNER_ACTION_REQUIRED
    payload = {"check_run": {"id": 99, "name": "required-approval", "status": "completed",
                             "conclusion": "action_required"},
              "repository": {"full_name": "acme/widgets"}}
    event = parse_webhook_event("check_run", payload)
    assert event.status == OWNER_ACTION_REQUIRED


def test_agent_tasks_waiting_for_user_is_owner_action_required(tmp_path):
    """The PRIMARY, GitHub-native signal for this status: the Agent Tasks
    API's own waiting_for_user state, which GitHub documents as exactly
    'genuinely needs the user' -- not merely 'a PR exists and nobody has
    reviewed it yet'."""
    from adapters.github.adapter import GitHubCloudAdapter
    from adapters.github.config import GitHubAdapterSettings
    from adapters.github.state import GitHubAdapterState
    from tests.github_fakes import FakeGitHubClient

    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets",
                     [{"id": "t1", "state": "waiting_for_user", "updated_at": "2026-01-01T00:00:00Z"}])
    settings = GitHubAdapterSettings(token="x", repos=("acme/widgets",), state_db_path=tmp_path / "s.db")
    adapter = GitHubCloudAdapter(settings, client=client, state=GitHubAdapterState(settings.state_db_path))
    events = adapter.poll()
    assert events[0].status == OWNER_ACTION_REQUIRED
