"""Minimal GitHub REST API client: stdlib urllib only (no third-party HTTP
dependency), mirroring reporter/telegram.py's own house style exactly. The
token is read once at construction and is NEVER logged, printed, or included
in any exception message this module raises -- every error path below
constructs its message from the response body/status only, never from the
request (which is the only place the token appears, in the Authorization
header).
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Optional


class GitHubApiError(Exception):
    """Raised for a GitHub API failure. Message text is safe (never includes
    the bearer token -- the token lives only in a request header, which this
    exception never echoes)."""


class GitHubRateLimited(GitHubApiError):
    """Raised specifically for a 403/429 rate-limit response, carrying the
    number of seconds the caller should wait before retrying (from GitHub's
    own Retry-After / X-RateLimit-Reset headers) so callers can back off
    correctly instead of hammering the API."""

    def __init__(self, message: str, retry_after_sec: float) -> None:
        super().__init__(message)
        self.retry_after_sec = retry_after_sec


@dataclass
class GitHubClient:
    token: str
    api_base_url: str = "https://api.github.com"
    api_version: str = "2026-03-10"
    timeout: float = 20.0

    def _request(self, method: str, path: str, *, params: Optional[dict[str, Any]] = None,
                body: Optional[dict[str, Any]] = None) -> Any:
        url = f"{self.api_base_url}{path}"
        if params:
            qs = "&".join(f"{k}={urllib.request.quote(str(v))}" for k, v in params.items() if v is not None)
            if qs:
                url = f"{url}?{qs}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": self.api_version,
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read()
                return json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            reset_at = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
            remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            if exc.code in (403, 429) and (retry_after or remaining == "0"):
                wait = float(retry_after) if retry_after else max(0.0, float(reset_at or 0) - time.time())
                raise GitHubRateLimited(f"GitHub API rate-limited (HTTP {exc.code})", retry_after_sec=wait) from exc
            try:
                detail = json.loads(exc.read()).get("message", "")
            except Exception:
                detail = ""
            raise GitHubApiError(f"GitHub API request failed: HTTP {exc.code} {detail}"[:300]) from exc
        except urllib.error.URLError as exc:
            raise GitHubApiError(f"GitHub API request failed: {type(exc).__name__}") from exc

    # ── Agent Tasks API (Copilot cloud agent) ───────────────────────────────
    # docs.github.com/rest/agent-tasks/agent-tasks -- public preview.
    def list_tasks(self, owner: str, repo: str, *, since: Optional[str] = None,
                   per_page: int = 100) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"per_page": per_page, "sort": "updated_at", "direction": "desc"}
        if since:
            params["since"] = since
        result = self._request("GET", f"/agents/repos/{owner}/{repo}/tasks", params=params)
        return (result or {}).get("tasks", [])

    def get_task(self, owner: str, repo: str, task_id: str) -> dict[str, Any]:
        return self._request("GET", f"/agents/repos/{owner}/{repo}/tasks/{task_id}")

    # ── reverse-instruction path: issue/PR comment ──────────────────────────
    # A PR is an issue for the comments endpoint -- this is GitHub's own API
    # shape, not a shortcut taken here. This is the documented, established
    # way to hand the coding agent more instructions (see README.md
    # "Reverse instruction path"); it is a plain comment post, never a merge,
    # push, or any other privileged action.
    def post_issue_comment(self, owner: str, repo: str, issue_number: int, body: str) -> dict[str, Any]:
        return self._request("POST", f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
                             body={"body": body})
