"""Outbound Telegram message formatting. Every notified status has a fixed,
predictable shape so the Owner can scan messages quickly on a phone: agent,
task, status, short summary, and an explicit "Owner action" line so it is
never ambiguous whether a reply is expected.
"""
from __future__ import annotations

_ICON = {
    "PASS": "✅",
    "BLOCKED": "🚫",
    "FAILED": "❌",
    "OWNER_ACTION_REQUIRED": "👤",
    "CANCELLED": "🛑",
    "TIMED_OUT": "⏱️",
    "QUEUED": "⏳",
    "WAITING": "⏳",
}

_OWNER_ACTION_LINE = {
    "PASS": "Owner action: none",
    "BLOCKED": "Owner action: review recommended",
    "FAILED": "Owner action: review recommended",
    "OWNER_ACTION_REQUIRED": "Owner action: REQUIRED — reply to this message to route an instruction back to this agent/task",
    "CANCELLED": "Owner action: none",
    "TIMED_OUT": "Owner action: review recommended",
    "QUEUED": "Owner action: none",
    "WAITING": "Owner action: none",
}


def format_notification(*, agent_name: str, agent_id: str, task_id: str, status: str,
                        summary: str, details: str | None, provider: str | None = None,
                        repository: str | None = None, branch: str | None = None) -> str:
    icon = _ICON.get(status, "ℹ️")
    lines = [f"{icon} {agent_name} ({agent_id}) — {status}"]
    if provider:
        lines.append(f"Provider: {provider}")
    if repository:
        lines.append(f"Repo: {repository}")
    if branch:
        lines.append(f"Branch: {branch}")
    lines.extend([f"Task: {task_id}", summary])
    if details:
        lines.append(details)
    lines.append(_OWNER_ACTION_LINE.get(status, "Owner action: none"))
    return "\n".join(lines)
