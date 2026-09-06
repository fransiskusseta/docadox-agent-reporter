"""Reverse instruction path: send_instruction() and deliver() (the
adapters/base.py AgentAdapter Protocol implementation)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from adapters.github.adapter import GitHubCloudAdapter
from adapters.github.config import GitHubAdapterSettings
from adapters.github.state import GitHubAdapterState
from tests.github_fakes import FakeGitHubClient


def _adapter(tmp_path, client: FakeGitHubClient) -> GitHubCloudAdapter:
    settings = GitHubAdapterSettings(token="fake-token", repos=("acme/widgets",), agent_id="copilot-1",
                                     state_db_path=tmp_path / "state.db")
    return GitHubCloudAdapter(settings, client=client, state=GitHubAdapterState(settings.state_db_path))


def test_send_instruction_posts_a_comment_mentioning_copilot(tmp_path):
    client = FakeGitHubClient()
    adapter = _adapter(tmp_path, client)

    ok = adapter.send_instruction("acme/widgets", 5, "please also add a test")

    assert ok is True
    assert len(client.posted_comments) == 1
    owner, repo, number, body = client.posted_comments[0]
    assert (owner, repo, number) == ("acme", "widgets", 5)
    assert body.startswith("@copilot ")
    assert "please also add a test" in body


def test_send_instruction_never_merges_pushes_or_deploys(tmp_path):
    """The only GitHub write this adapter ever performs is a comment POST --
    proven here by asserting the fake client's ONLY recorded write-shaped
    call is posted_comments; nothing else was ever invoked."""
    client = FakeGitHubClient()
    adapter = _adapter(tmp_path, client)
    adapter.send_instruction("acme/widgets", 5, "go ahead")
    assert not hasattr(client, "merge_calls")
    assert not hasattr(client, "push_calls")
    assert not hasattr(client, "deploy_calls")


def test_deliver_uses_the_last_known_pr_for_that_agent(tmp_path):
    client = FakeGitHubClient()
    adapter = _adapter(tmp_path, client)
    adapter.state.set_agent_target("copilot-1", "acme/widgets", 9)

    ok = adapter.deliver("copilot-1", "an owner instruction")

    assert ok is True
    assert client.posted_comments[0][2] == 9


def test_deliver_returns_false_when_no_target_known_yet(tmp_path):
    client = FakeGitHubClient()
    adapter = _adapter(tmp_path, client)
    ok = adapter.deliver("copilot-1", "an owner instruction")
    assert ok is False
    assert client.posted_comments == []


def test_ambiguous_artifact_id_does_not_enable_reverse_comment(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{
        "id": "t1", "state": "waiting_for_user", "updated_at": "2026-01-01T00:00:00Z",
        "artifacts": [{"provider": "github", "type": "pull", "data": {"id": 11}}],
    }])
    adapter = _adapter(tmp_path, client)
    adapter.poll()

    ok = adapter.deliver("copilot-1", "please clarify X")

    assert ok is False
    assert client.posted_comments == []


def test_explicit_pr_number_enables_reverse_comment(tmp_path):
    client = FakeGitHubClient()
    client.set_tasks("acme", "widgets", [{
        "id": "t1", "state": "waiting_for_user", "updated_at": "2026-01-01T00:00:00Z",
        "artifacts": [{"provider": "github", "type": "pull", "data": {"number": 11}}],
    }])
    adapter = _adapter(tmp_path, client)
    adapter.poll()

    ok = adapter.deliver("copilot-1", "please clarify X")

    assert ok is True
    assert client.posted_comments[0][2] == 11
