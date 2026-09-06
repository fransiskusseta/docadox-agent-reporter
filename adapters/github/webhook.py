"""Generic GitHub webhook ingestion: signature verification (real, GitHub-
issued HMAC-SHA256) and payload -> NormalizedEvent mapping for the events
this task lists as candidates: pull_request, check_suite, check_run,
workflow_run, issue_comment.

This module never executes anything from the payload -- it only reads JSON
fields and returns a NormalizedEvent (or None). No subprocess, no eval, no
dynamic import.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any, Optional

from .models import (
    CANCELLED,
    CHECK_CONCLUSION_MAP,
    CHECK_STATUS_MAP,
    PASS,
    PULL_REQUEST_ACTION_MAP,
    NormalizedEvent,
)

SUPPORTED_EVENTS = frozenset({"pull_request", "check_suite", "check_run", "workflow_run", "issue_comment"})


def verify_signature(secret: str, raw_body: bytes, signature_header: Optional[str]) -> bool:
    """Verifies GitHub's `X-Hub-Signature-256: sha256=<hex>` header. Returns
    False (never raises) for a missing header, wrong prefix, or mismatch --
    the caller is responsible for rejecting the request on a False result.
    Constant-time comparison throughout; the secret itself is never included
    in any return value or exception."""
    if not secret or not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    provided = signature_header[len("sha256="):]
    return hmac.compare_digest(expected, provided)


def _task_id_for(kind: str, payload: dict[str, Any]) -> str:
    # A stable, human-inspectable task_id per underlying GitHub object --
    # never a fabricated id, always one GitHub itself assigns.
    if kind in ("check_suite", "check_run"):
        obj = payload.get(kind, {})
        return f"{kind}:{obj.get('id')}"
    if kind == "workflow_run":
        return f"workflow_run:{payload.get('workflow_run', {}).get('id')}"
    if kind == "pull_request":
        return f"pr:{payload.get('number')}"
    if kind == "issue_comment":
        return f"issue:{payload.get('issue', {}).get('number')}"
    return f"{kind}:unknown"


def parse_webhook_event(event_type: str, payload: dict[str, Any]) -> Optional[NormalizedEvent]:
    """Returns None for an event type this adapter does not translate into a
    status change (e.g. issue_comment on its own never implies a status),
    or for an action/state this adapter has no confident mapping for --
    never a guessed status."""
    repo_full_name = (payload.get("repository") or {}).get("full_name")

    if event_type == "pull_request":
        action = payload.get("action")
        pr = payload.get("pull_request", {})
        pr_number = pr.get("number")
        if action == "closed":
            status = PASS if pr.get("merged") else CANCELLED
        else:
            status = PULL_REQUEST_ACTION_MAP.get(action)
        if status is None:
            return None
        return NormalizedEvent(
            agent_id="", task_id=_task_id_for("pull_request", payload), status=status,
            summary=f"Pull request #{pr_number} {action} in {repo_full_name}",
            source=f"webhook:pull_request:{action}", raw_state=action,
            pr_number=pr_number, repo_full_name=repo_full_name,
        )

    if event_type in ("check_suite", "check_run"):
        obj = payload.get(event_type, {})
        gh_status = obj.get("status")
        conclusion = obj.get("conclusion")
        status = CHECK_STATUS_MAP.get(gh_status)
        if status is None and gh_status == "completed":
            status = CHECK_CONCLUSION_MAP.get(conclusion)
        if status is None:
            return None
        name = obj.get("name") or event_type
        pr_number = (obj.get("pull_requests") or [{}])[0].get("number") if obj.get("pull_requests") else None
        return NormalizedEvent(
            agent_id="", task_id=_task_id_for(event_type, payload), status=status,
            summary=f"{name}: {gh_status}" + (f" ({conclusion})" if conclusion else "") + f" in {repo_full_name}",
            source=f"webhook:{event_type}", raw_state=f"{gh_status}/{conclusion}",
            pr_number=pr_number, repo_full_name=repo_full_name,
        )

    if event_type == "workflow_run":
        run = payload.get("workflow_run", {})
        gh_status = run.get("status")
        conclusion = run.get("conclusion")
        status = CHECK_STATUS_MAP.get(gh_status)
        if status is None and gh_status == "completed":
            status = CHECK_CONCLUSION_MAP.get(conclusion)
        if status is None:
            return None
        pr_number = (run.get("pull_requests") or [{}])[0].get("number") if run.get("pull_requests") else None
        return NormalizedEvent(
            agent_id="", task_id=_task_id_for("workflow_run", payload), status=status,
            summary=f"Workflow '{run.get('name')}': {gh_status}"
                   + (f" ({conclusion})" if conclusion else "") + f" in {repo_full_name}",
            source="webhook:workflow_run", raw_state=f"{gh_status}/{conclusion}",
            pr_number=pr_number, repo_full_name=repo_full_name,
        )

    # issue_comment: informational only (e.g. correlating an Owner-facing
    # reply on the issue/PR itself) -- never synthesizes a status change on
    # its own, per this task's own "if used for agent task assignment" scope
    # note; no confident status mapping exists for a plain comment.
    return None
