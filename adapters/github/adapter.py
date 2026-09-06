"""GitHubCloudAdapter: ties client + state + webhook/hook parsing together
into the interface this task asked for -- poll(), receive_webhook(),
normalize_event(), get_status(), send_instruction() -- plus deliver() so
this class is also a drop-in implementation of adapters/base.py's own
AgentAdapter Protocol (the reporter core's existing, stable adapter
interface for the reverse-instruction path).

This module makes exactly two kinds of outbound network call: to GitHub's
API (client.py) and to the reporter's own event-ingestion endpoint (local
reporter API by default, or the Cloud Gateway if configured) -- never
anything else, never a shell command, never eval.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from .client import GitHubApiError, GitHubClient
from .config import GitHubAdapterSettings
from .copilot_hooks import parse_hook_event, verify_hook_secret
from .models import AGENT_TASK_STATE_MAP, NormalizedEvent
from .state import GitHubAdapterState
from .webhook import parse_webhook_event, verify_signature


class WebhookSignatureInvalid(Exception):
    """Raised by receive_webhook()/receive_copilot_hook() for a request that
    fails signature/shared-secret verification. Callers (server.py) must
    respond 401 and must NOT process the body."""


def _message_id_for(event: NormalizedEvent) -> str:
    """Deterministic idempotency key for the Gateway sink.

    GitHub delivery IDs distinguish two real lifecycle events with identical
    normalized content (for example, close -> reopen -> close on one PR),
    while the adapter's durable delivery table still suppresses a redelivery
    of the same GitHub delivery.
    """
    delivery_id = event.extra.get("delivery_id", "")
    raw = "\x1f".join([
        event.agent_id, event.task_id, event.status, event.summary, event.details or "", delivery_id,
    ])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:64]


class GitHubCloudAdapter:
    def __init__(self, settings: GitHubAdapterSettings, *, client: Optional[GitHubClient] = None,
                state: Optional[GitHubAdapterState] = None) -> None:
        self.settings = settings
        self.client = client or GitHubClient(
            token=settings.token, api_base_url=settings.api_base_url, api_version=settings.api_version,
        )
        self.state = state or GitHubAdapterState(settings.state_db_path)

    # ── normalize_event: fills in the reporting agent's own identity ───────
    def normalize_event(self, event: NormalizedEvent) -> NormalizedEvent:
        if event.agent_id:
            return event
        from dataclasses import replace
        return replace(event, agent_id=self.settings.agent_id, agent_name=event.agent_name or self.settings.agent_name)

    # ── PRIMARY: Copilot cloud agent Agent Tasks API polling ────────────────
    def poll(self) -> list[NormalizedEvent]:
        """Polls every configured repo's Agent Tasks list, compares each
        task's state against the last one persisted for it, and returns only
        the tasks whose canonical status genuinely changed since the last
        poll -- never re-emits an unchanged state. Raises GitHubRateLimited/
        GitHubApiError on a genuine API failure; the caller (poll_runner.py)
        is responsible for backoff -- this method never sleeps itself, and
        never loses the persisted cursor on a failure (the cursor is only
        advanced after a repo's tasks are successfully fetched)."""
        emitted: list[NormalizedEvent] = []
        for repo in self.settings.repos:
            owner, _, name = repo.partition("/")
            if not owner or not name:
                continue
            since = self.state.get_cursor(repo)
            tasks = self.client.list_tasks(owner, name, since=since)
            latest_updated = since
            for task in tasks:
                task_id = str(task.get("id"))
                raw_state = task.get("state", "")
                status = AGENT_TASK_STATE_MAP.get(raw_state)
                updated_at = task.get("updated_at")
                if updated_at and (latest_updated is None or updated_at > latest_updated):
                    latest_updated = updated_at
                if status is None:
                    continue  # an undocumented state value -- never guessed at
                pr_number = _extract_pr_number(task)
                prior = self.state.last_known_status(repo, task_id)
                if status == prior:
                    continue
                event = self.normalize_event(NormalizedEvent(
                    agent_id="", task_id=task_id, status=status,
                    summary=f"Copilot cloud agent task {task_id} in {repo}: {raw_state}",
                    source="agent_tasks_poll", raw_state=raw_state, pr_number=pr_number, repo_full_name=repo,
                ))
                emitted.append(event)
                self.state.record_task_status(repo, task_id, status, raw_state, pr_number)
                if pr_number is not None:
                    self.state.set_agent_target(event.agent_id, repo, pr_number)
            if latest_updated:
                self.state.set_cursor(repo, latest_updated)
        return emitted

    # ── generic GitHub webhook ingestion ────────────────────────────────────
    def receive_webhook(self, *, event_type: str, signature_header: Optional[str], raw_body: bytes,
                        delivery_id: Optional[str] = None) -> Optional[dict]:
        if not verify_signature(self.settings.webhook_secret, raw_body, signature_header):
            raise WebhookSignatureInvalid("invalid_github_webhook_signature")
        if delivery_id and not self.state.mark_delivery_seen(f"webhook:{delivery_id}"):
            return None  # already processed this exact GitHub delivery -- no duplicate event
        payload = json.loads(raw_body.decode("utf-8"))
        parsed = parse_webhook_event(event_type, payload)
        if parsed is None:
            return None
        event = self.normalize_event(parsed)
        if delivery_id:
            from dataclasses import replace
            event = replace(event, extra={**event.extra, "delivery_id": delivery_id})
        if event.pr_number is not None and event.repo_full_name:
            self.state.set_agent_target(event.agent_id, event.repo_full_name, event.pr_number)
        return self.report(event)

    # ── Copilot Cloud HTTP hook ingestion (separate auth model) ─────────────
    def receive_copilot_hook(self, *, event_name: str, secret_header: Optional[str],
                             payload: dict[str, Any], delivery_id: Optional[str] = None) -> Optional[dict]:
        if not verify_hook_secret(self.settings.copilot_hook_secret, secret_header):
            raise WebhookSignatureInvalid("invalid_copilot_hook_secret")
        if delivery_id and not self.state.mark_delivery_seen(f"hook:{delivery_id}"):
            return None
        parsed = parse_hook_event(event_name, payload)
        if parsed is None:
            return None
        event = self.normalize_event(parsed)
        if delivery_id:
            from dataclasses import replace
            event = replace(event, extra={**event.extra, "delivery_id": delivery_id})
        return self.report(event)

    # ── get_status: on-demand single-task lookup (not just poll's batch) ────
    def get_status(self, repo: str, task_id: str) -> dict:
        owner, _, name = repo.partition("/")
        task = self.client.get_task(owner, name, task_id)
        raw_state = task.get("state", "")
        status = AGENT_TASK_STATE_MAP.get(raw_state, "UNKNOWN")
        return {"repo": repo, "task_id": task_id, "status": status, "raw_state": raw_state, "task": task}

    # ── reverse instruction path ────────────────────────────────────────────
    def send_instruction(self, repo: str, pr_or_issue_number: int, message_text: str) -> bool:
        """The narrowest safe method GitHub genuinely supports (verified:
        mentioning @copilot in an issue/PR comment is GitHub's own
        documented way to hand the coding agent more instructions -- there
        is no API to inject text into an already-running task). This is a
        plain comment POST -- never a merge, push, deploy, or any other
        privileged action; Reporter's own approval.py boundary is untouched:
        this adapter transports the Owner's words verbatim, it does not
        decide anything."""
        owner, _, name = repo.partition("/")
        try:
            self.client.post_issue_comment(owner, name, pr_or_issue_number, f"@copilot {message_text}")
            return True
        except GitHubApiError:
            return False

    def deliver(self, agent_id: str, message_text: str) -> bool:
        """adapters/base.py AgentAdapter Protocol implementation. Returns
        False (never raises) when no known open PR/issue target exists for
        this agent_id yet -- the caller must leave the inbox entry
        unacknowledged for later polling, exactly like the tmux adapter's
        own contract when its target session does not exist."""
        target = self.state.get_agent_target(agent_id)
        if target is None:
            return False
        return self.send_instruction(target["repo"], target["pr_number"], message_text)

    # ── sink: where a normalized event actually gets reported ──────────────
    def report(self, event: NormalizedEvent) -> dict:
        if self.settings.sink == "gateway":
            return self._send_to_gateway(event)
        return self._send_to_local(event)

    def _send_to_local(self, event: NormalizedEvent) -> dict:
        # message_id (reporter/store.py's own content-independent idempotency
        # key, added concurrently to this task) is included so a retried
        # delivery -- e.g. this adapter's own report() being called twice for
        # the same GitHub state after a network blip on the first attempt --
        # can never double-notify, even in the rare case where two distinct
        # underlying events happen to produce identical summary text (which
        # would otherwise collide under the older content-hash-only dedup).
        body = {
            "agent_id": event.agent_id, "agent_name": event.agent_name, "task_id": event.task_id,
            "status": event.status, "summary": event.summary, "message_id": _message_id_for(event),
        }
        if event.details:
            body["details"] = event.details
        if event.timestamp:
            body["timestamp"] = event.timestamp
        return _post_json(f"{self.settings.reporter_base_url}/v1/events", body)

    def _send_to_gateway(self, event: NormalizedEvent) -> dict:
        body = {
            "message_id": _message_id_for(event), "agent_id": event.agent_id,
            "agent_name": event.agent_name, "task_id": event.task_id, "status": event.status,
            "summary": event.summary,
        }
        if event.details:
            body["details"] = event.details
        if event.timestamp:
            body["timestamp"] = event.timestamp
        path = "/v1/cloud/events"
        payload = json.dumps(body).encode("utf-8")
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        signature = _gateway_signature(self.settings.gateway_adapter_secret, timestamp, nonce, "POST", path, payload)
        return _post_json(
            f"{self.settings.gateway_url}{path}", body,
            extra_headers={
                "X-Reporter-Timestamp": timestamp,
                "X-Reporter-Nonce": nonce,
                "X-Reporter-Signature": signature,
            },
        )


def _extract_pr_number(task: dict[str, Any]) -> Optional[int]:
    """Extract only an explicitly named per-repository PR number.

    The Agent Tasks schema exposes ``artifacts[].data.id`` without defining
    whether it is a PR number or a global database id. It is therefore never
    used for reverse comments. A task or artifact may be used only when the
    API response explicitly supplies a ``number`` field.
    """
    direct = task.get("pull_request") or task.get("pullRequest")
    if isinstance(direct, dict) and isinstance(direct.get("number"), int) and direct["number"] > 0:
        return direct["number"]
    for artifact in task.get("artifacts") or []:
        if artifact.get("type") == "pull":
            data = artifact.get("data") or {}
            if isinstance(data.get("number"), int) and data["number"] > 0:
                return data["number"]
    return None


def _gateway_signature(secret: str, timestamp: str, nonce: str, method: str, path: str, body: bytes) -> str:
    import hashlib as _hashlib
    import hmac as _hmac
    return _hmac.new(
        secret.encode(), b"\n".join([timestamp.encode(), nonce.encode(), method.encode(), path.encode(), body]),
        _hashlib.sha256,
    ).hexdigest()


def _post_json(url: str, body: dict[str, Any], *, extra_headers: Optional[dict[str, str]] = None) -> dict:
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {"error": str(exc)}
        raise GitHubApiError(f"reporting sink rejected event: HTTP {exc.code} {detail}"[:300]) from exc
    except urllib.error.URLError as exc:
        raise GitHubApiError(f"could not reach reporting sink: {type(exc).__name__}") from exc
