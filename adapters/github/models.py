"""Canonical event model for the GitHub Cloud Agent Adapter.

CANONICAL_STATUSES is the full 10-value vocabulary this adapter speaks. It is
a superset of reporter/api.py's own status enum -- reporter/api.py,
reporter/notifications.py, reporter/formatting.py and reporter_cli.py were
extended (narrowly, additively, no redesign) to accept all 10 values. The
Cloud Gateway uses this same ten-value contract, so both sinks preserve the
status without provider-specific aliases.

Every mapping table here is built from verified GitHub documentation (see
adapters/github/README.md "Sources"), never invented: a state this adapter
cannot map with confidence is reported as UNKNOWN rather than guessed at.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# ── canonical vocabulary (this task's own contract) ──────────────────────────
IDLE = "IDLE"
QUEUED = "QUEUED"
RUNNING = "RUNNING"
WAITING = "WAITING"
OWNER_ACTION_REQUIRED = "OWNER_ACTION_REQUIRED"
PASS = "PASS"
BLOCKED = "BLOCKED"
FAILED = "FAILED"
CANCELLED = "CANCELLED"
TIMED_OUT = "TIMED_OUT"
UNKNOWN = "UNKNOWN"  # never sent onward -- see adapter.py; exists so a mapper
                     # can return "I don't know" instead of a guessed value.

CANONICAL_STATUSES = frozenset({
    IDLE, QUEUED, RUNNING, WAITING, OWNER_ACTION_REQUIRED, PASS, BLOCKED, FAILED, CANCELLED, TIMED_OUT,
})

# ── GitHub Copilot cloud agent "Agent Tasks" REST API state -> canonical ────
# Verified against docs.github.com/rest/agent-tasks/agent-tasks (2026-03-10)
# and the 2026-05/06 GitHub changelog entries announcing this API. State enum
# is EXACTLY: queued | in_progress | completed | failed | idle |
# waiting_for_user | timed_out | cancelled -- no other value is ever mapped.
AGENT_TASK_STATE_MAP = {
    "queued": QUEUED,
    "in_progress": RUNNING,
    "idle": IDLE,
    "waiting_for_user": OWNER_ACTION_REQUIRED,  # GitHub's own explicit "needs you" signal
    "completed": PASS,
    "failed": FAILED,
    "timed_out": TIMED_OUT,
    "cancelled": CANCELLED,
}

# ── classic GitHub webhook status/conclusion -> canonical (secondary path) ──
# check_run / check_suite / workflow_run all share this status+conclusion
# shape. "action_required" is GitHub's own conclusion value for exactly the
# case this task calls out: a genuine block on a human decision (e.g. a
# required reviewer approval gate on a workflow), never merely "PR awaiting
# review" -- see README.md "Owner action detection" for why the two are kept
# distinct.
CHECK_STATUS_MAP = {"queued": QUEUED, "in_progress": RUNNING, "completed": None}  # completed defers to conclusion
CHECK_CONCLUSION_MAP = {
    "success": PASS,
    "failure": FAILED,
    "neutral": PASS,
    "cancelled": CANCELLED,
    "timed_out": TIMED_OUT,
    "action_required": OWNER_ACTION_REQUIRED,
    "stale": CANCELLED,
    "skipped": CANCELLED,
}

# pull_request action -> canonical (only the subset that represents genuine
# agent-work lifecycle, not every PR action GitHub fires).
PULL_REQUEST_ACTION_MAP = {
    "opened": RUNNING,
    "reopened": RUNNING,
    "synchronize": RUNNING,
    "ready_for_review": RUNNING,
    # "closed" is ambiguous without the payload's own merged flag -- resolved
    # in webhook.py (merged -> PASS, not merged -> CANCELLED), never guessed
    # at here.
}


@dataclass(frozen=True)
class NormalizedEvent:
    """The one shape every ingestion path (poll/webhook/hook) converges on
    before being sent to a sink (adapter.py). `source` and `raw_state` are
    for audit/debugging only -- never sent to the reporter as-is; `summary`
    is what actually appears in a Telegram notification."""

    agent_id: str
    task_id: str
    status: str
    summary: str
    details: Optional[str] = None
    agent_name: Optional[str] = None
    timestamp: Optional[str] = None
    source: str = "unknown"       # "agent_tasks_poll" | "webhook:<event>" | "copilot_hook:<name>"
    raw_state: Optional[str] = None
    pr_number: Optional[int] = None
    repo_full_name: Optional[str] = None
    extra: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in CANONICAL_STATUSES:
            raise ValueError(f"NormalizedEvent.status must be one of {sorted(CANONICAL_STATUSES)}, "
                             f"got {self.status!r}")
