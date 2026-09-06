"""Test doubles for the GitHub Cloud Agent Adapter suite. No test in this
suite ever reaches the real network -- GitHubClient's own HTTP methods are
overridden, and the local-sink/gateway-sink HTTP POST helper is monkeypatched
at the call site in each test that needs to inspect what would have been sent.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from adapters.github.client import GitHubApiError, GitHubClient, GitHubRateLimited


@dataclass
class FakeGitHubClient(GitHubClient):
    _tasks_by_repo: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    _get_task_result: Optional[dict[str, Any]] = None
    _raise_on_list: Optional[Exception] = None
    posted_comments: list[tuple[str, str, int, str]] = field(default_factory=list)

    def __init__(self) -> None:
        super().__init__(token="fake-token-for-tests-only-not-a-real-secret")
        self._tasks_by_repo = {}
        self._get_task_result = None
        self._raise_on_list = None
        self.posted_comments = []
        self.list_calls: list[tuple[str, str, Optional[str]]] = []

    def set_tasks(self, owner: str, repo: str, tasks: list[dict[str, Any]]) -> None:
        self._tasks_by_repo[f"{owner}/{repo}"] = tasks

    def raise_next_list(self, exc: Exception) -> None:
        self._raise_on_list = exc

    def list_tasks(self, owner: str, repo: str, *, since=None, per_page: int = 100):
        self.list_calls.append((owner, repo, since))
        if self._raise_on_list is not None:
            exc, self._raise_on_list = self._raise_on_list, None
            raise exc
        return self._tasks_by_repo.get(f"{owner}/{repo}", [])

    def get_task(self, owner: str, repo: str, task_id: str):
        return self._get_task_result or {}

    def post_issue_comment(self, owner: str, repo: str, issue_number: int, body: str):
        self.posted_comments.append((owner, repo, issue_number, body))
        return {"id": 1, "body": body}


__all__ = ["FakeGitHubClient", "GitHubApiError", "GitHubRateLimited"]
