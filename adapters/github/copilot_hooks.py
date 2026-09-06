"""Copilot Cloud HTTP hook ingestion -- deliberately SEPARATE from
webhook.py because the authentication/signature model genuinely differs:

GitHub's own hooks-reference documentation (docs.github.com/en/copilot/
reference/hooks-reference, verified during this task) describes NO signing
or authentication scheme for these HTTP hooks at all -- unlike standard
GitHub webhooks (X-Hub-Signature-256, a real HMAC over the body, verified in
webhook.py). A hook is configured per-repository in a committed
`.github/hooks/*.json` file, which supports an arbitrary `headers` map sent
with every hook POST. This module verifies a shared-secret HEADER value the
Owner sets in both places -- a real check, but explicitly NOT a
cryptographic signature over the body, and this limitation is documented
here rather than silently presented as equivalent to webhook.py's guarantee.

Only session-lifecycle events are translated into a status change
(sessionStart/SessionStart, sessionEnd/SessionEnd, agentStop/Stop,
errorOccurred/ErrorOccurred). Fine-grained tool-execution events
(preToolUse/postToolUse/subagentStart/etc.) are intentionally NOT mapped to
a status event each -- translating every one of those would be exactly the
"one Telegram-style event per low-level event" spam this task explicitly
warns against.
"""
from __future__ import annotations

import hmac
from typing import Any, Optional

from .models import CANCELLED, FAILED, IDLE, PASS, RUNNING, NormalizedEvent


def verify_hook_secret(configured_secret: str, header_value: Optional[str]) -> bool:
    """Constant-time shared-secret comparison. Returns False (never raises)
    if no secret is configured at all -- an unconfigured secret must never
    be treated as "any request is valid"."""
    if not configured_secret or not header_value:
        return False
    return hmac.compare_digest(configured_secret, header_value)


def parse_hook_event(event_name: str, payload: dict[str, Any]) -> Optional[NormalizedEvent]:
    key = event_name.strip().lower()
    session_id = payload.get("sessionId") or payload.get("session_id") or "unknown-session"

    if key in ("sessionstart",):
        prompt = (payload.get("initialPrompt") or "")[:200]
        return NormalizedEvent(
            agent_id="", task_id=f"copilot-session:{session_id}", status=RUNNING,
            summary=f"Copilot cloud session started" + (f": {prompt}" if prompt else ""),
            source="copilot_hook:sessionStart", raw_state=event_name,
        )

    if key in ("sessionend",):
        # The reference payload for sessionEnd does not guarantee an outcome
        # field; if one is present, honor it, otherwise report PASS (session
        # ended without an errorOccurred hook having fired is the only
        # signal this hook alone can honestly give).
        outcome = (payload.get("outcome") or payload.get("result") or "").lower()
        status = {"success": PASS, "error": FAILED, "cancelled": CANCELLED}.get(outcome, PASS)
        return NormalizedEvent(
            agent_id="", task_id=f"copilot-session:{session_id}", status=status,
            summary="Copilot cloud session ended",
            source="copilot_hook:sessionEnd", raw_state=event_name,
        )

    if key in ("agentstop", "stop"):
        return NormalizedEvent(
            agent_id="", task_id=f"copilot-session:{session_id}", status=IDLE,
            summary="Copilot cloud agent turn finished",
            source="copilot_hook:agentStop", raw_state=event_name,
        )

    if key in ("erroroccurred",):
        message = (payload.get("error") or payload.get("message") or "")[:300]
        return NormalizedEvent(
            agent_id="", task_id=f"copilot-session:{session_id}", status=FAILED,
            summary="Copilot cloud session error" + (f": {message}" if message else ""),
            source="copilot_hook:errorOccurred", raw_state=event_name,
        )

    # Every other documented hook (userPromptSubmitted, preToolUse,
    # postToolUse, postToolUseFailure, subagentStart/Stop, preCompact) is
    # deliberately not translated into a status event -- see module
    # docstring.
    return None
