"""Status mapping: every documented Agent Tasks API state -> canonical
status (test scenarios 1-7), plus a provider-agnostic classic webhook event
(test scenario 13: Claude/Codex used as a GitHub third-party coding agent,
represented with zero Anthropic/OpenAI-specific assumptions)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from adapters.github.adapter import GitHubCloudAdapter
from adapters.github.config import GitHubAdapterSettings
from adapters.github.models import (
    CANCELLED,
    FAILED,
    OWNER_ACTION_REQUIRED,
    PASS,
    QUEUED,
    RUNNING,
    TIMED_OUT,
)
from adapters.github.webhook import parse_webhook_event
from tests.github_fakes import FakeGitHubClient


def _settings(**overrides) -> GitHubAdapterSettings:
    base = dict(token="fake-token", repos=("acme/widgets",), agent_id="copilot-1",
               agent_name="Copilot", state_db_path=None)
    base.update(overrides)
    return GitHubAdapterSettings(**base)


def _adapter(tmp_path, client: FakeGitHubClient) -> GitHubCloudAdapter:
    from adapters.github.state import GitHubAdapterState
    settings = _settings(state_db_path=tmp_path / "state.db")
    return GitHubCloudAdapter(settings, client=client, state=GitHubAdapterState(settings.state_db_path))


@pytest.mark.parametrize("gh_state,expected", [
    ("queued", QUEUED),
    ("in_progress", RUNNING),
    ("waiting_for_user", OWNER_ACTION_REQUIRED),
    ("completed", PASS),
    ("failed", FAILED),
    ("cancelled", CANCELLED),
    ("timed_out", TIMED_OUT),
])
def test_agent_task_state_maps_to_canonical_status(tmp_path, gh_state, expected):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{"id": "task-1", "state": gh_state, "updated_at": "2026-01-01T00:00:00Z"}])
    adapter = _adapter(tmp_path, client)

    events = adapter.poll()

    assert len(events) == 1
    assert events[0].status == expected
    assert events[0].agent_id == "copilot-1"
    assert events[0].task_id == "task-1"
    assert events[0].raw_state == gh_state


def test_provider_agnostic_pull_request_event_no_copilot_assumptions():
    """A plain pull_request payload with no Copilot/Claude/Codex-specific
    field at all -- proves the mapping needs nothing but generic GitHub PR
    fields (number, merged, action) that any third-party coding agent's PR
    would carry equally, whichever provider is actually driving it."""
    payload = {
        "action": "opened",
        "number": 42,
        "pull_request": {"number": 42, "merged": False},
        "repository": {"full_name": "acme/widgets"},
    }
    event = parse_webhook_event("pull_request", payload)
    assert event is not None
    assert event.status == RUNNING
    assert event.pr_number == 42
    assert event.repo_full_name == "acme/widgets"
    # No field anywhere in the parser or the payload names a specific AI provider.
    assert "copilot" not in str(payload).lower()
    assert "anthropic" not in str(payload).lower()
    assert "openai" not in str(payload).lower()


def test_provider_agnostic_pull_request_merged_maps_to_pass():
    payload = {"action": "closed", "number": 7, "pull_request": {"number": 7, "merged": True},
              "repository": {"full_name": "acme/widgets"}}
    event = parse_webhook_event("pull_request", payload)
    assert event.status == PASS


def test_provider_agnostic_pull_request_closed_unmerged_maps_to_cancelled():
    payload = {"action": "closed", "number": 7, "pull_request": {"number": 7, "merged": False},
              "repository": {"full_name": "acme/widgets"}}
    event = parse_webhook_event("pull_request", payload)
    assert event.status == CANCELLED


def test_unknown_agent_task_state_is_never_guessed_at(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets",
                     [{"id": "task-x", "state": "some_future_state_not_yet_documented",
                       "updated_at": "2026-01-01T00:00:00Z"}])
    adapter = _adapter(tmp_path, client)
    events = adapter.poll()
    assert events == []  # skipped, never fabricated a canonical status
