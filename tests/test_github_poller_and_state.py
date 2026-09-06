"""Polling dedup, restart-resume, and rate-limit/error resilience
(test scenarios 8, 11, 12)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.github.adapter import GitHubCloudAdapter
from adapters.github.client import GitHubApiError, GitHubRateLimited
from adapters.github.config import GitHubAdapterSettings
from adapters.github.models import PASS, QUEUED
from adapters.github.state import GitHubAdapterState
from tests.github_fakes import FakeGitHubClient


def _settings(tmp_path) -> GitHubAdapterSettings:
    return GitHubAdapterSettings(token="fake-token", repos=("acme/widgets",), agent_id="copilot-1",
                                 state_db_path=tmp_path / "state.db")


# ── scenario 8: duplicate state does not create a duplicate event ──────────
def test_unchanged_task_state_across_polls_emits_nothing_twice(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{"id": "t1", "state": "queued", "updated_at": "2026-01-01T00:00:00Z"}])
    settings = _settings(tmp_path)
    adapter = GitHubCloudAdapter(settings, client=client, state=GitHubAdapterState(settings.state_db_path))

    first = adapter.poll()
    second = adapter.poll()

    assert len(first) == 1
    assert first[0].status == QUEUED
    assert second == []  # identical state the second time -- no duplicate event


def test_changed_state_between_polls_does_emit_a_new_event(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{"id": "t1", "state": "queued", "updated_at": "2026-01-01T00:00:00Z"}])
    settings = _settings(tmp_path)
    adapter = GitHubCloudAdapter(settings, client=client, state=GitHubAdapterState(settings.state_db_path))
    adapter.poll()

    client.set_tasks("acme", "widgets", [{"id": "t1", "state": "in_progress", "updated_at": "2026-01-01T00:05:00Z"}])
    second = adapter.poll()

    assert len(second) == 1
    assert second[0].status == "RUNNING"


# ── scenario 11: polling resumes from persisted state after "restart" ──────
def test_polling_resumes_from_persisted_cursor_after_restart(tmp_path):
    db_path = tmp_path / "state.db"
    client1 = FakeGitHubClient()
    client1.set_tasks("acme", "widgets", [{"id": "t1", "state": "completed", "updated_at": "2026-01-02T00:00:00Z"}])
    settings = GitHubAdapterSettings(token="fake-token", repos=("acme/widgets",), agent_id="copilot-1",
                                     state_db_path=db_path)
    adapter1 = GitHubCloudAdapter(settings, client=client1, state=GitHubAdapterState(db_path))
    events1 = adapter1.poll()
    assert len(events1) == 1
    assert events1[0].status == PASS

    # Simulate a full process restart: brand new adapter instance, brand new
    # GitHubAdapterState object, but pointed at the SAME on-disk db file.
    client2 = FakeGitHubClient()
    client2.set_tasks("acme", "widgets", [{"id": "t1", "state": "completed", "updated_at": "2026-01-02T00:00:00Z"}])
    adapter2 = GitHubCloudAdapter(settings, client=client2, state=GitHubAdapterState(db_path))
    events2 = adapter2.poll()

    assert events2 == []  # unchanged task state survived the "restart" -- no re-notification
    # The cursor persisted too: the second instance's list_tasks call carried
    # the timestamp the FIRST instance advanced to, not None/empty.
    assert client2.list_calls[0][2] == "2026-01-02T00:00:00Z"


# ── scenario 12: GitHub API rate-limit/error does not lose prior state ─────
def test_rate_limit_error_during_poll_does_not_corrupt_prior_state(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{"id": "t1", "state": "queued", "updated_at": "2026-01-01T00:00:00Z"}])
    settings = _settings(tmp_path)
    state = GitHubAdapterState(settings.state_db_path)
    adapter = GitHubCloudAdapter(settings, client=client, state=state)
    adapter.poll()  # establishes prior state: t1 -> QUEUED, cursor -> 2026-01-01T00:00:00Z

    prior_cursor = state.get_cursor("acme/widgets")
    prior_status = state.last_known_status("acme/widgets", "t1")

    client.raise_next_list(GitHubRateLimited("rate limited", retry_after_sec=30))
    try:
        adapter.poll()
        raised = False
    except GitHubRateLimited:
        raised = True
    assert raised is True

    # Prior state is completely intact after the failed cycle.
    assert state.get_cursor("acme/widgets") == prior_cursor
    assert state.last_known_status("acme/widgets", "t1") == prior_status == QUEUED


def test_generic_api_error_during_poll_does_not_corrupt_prior_state(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{"id": "t1", "state": "queued", "updated_at": "2026-01-01T00:00:00Z"}])
    settings = _settings(tmp_path)
    state = GitHubAdapterState(settings.state_db_path)
    adapter = GitHubCloudAdapter(settings, client=client, state=state)
    adapter.poll()
    prior_cursor = state.get_cursor("acme/widgets")

    client.raise_next_list(GitHubApiError("HTTP 500"))
    try:
        adapter.poll()
    except GitHubApiError:
        pass

    assert state.get_cursor("acme/widgets") == prior_cursor
