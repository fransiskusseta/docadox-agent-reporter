"""Small provider-neutral terminal completion reporter.

Credentials are read only from the environment. The command exits non-zero
when the bounded callback cannot be delivered, so a calling task can include
that fact in its final output without changing its implementation result.
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

STATUSES = {"PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "CANCELLED", "TIMED_OUT"}


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Report one cloud-agent terminal completion")
    for name in ("provider", "agent-id", "task-id", "status", "summary", "repository", "branch"):
        p.add_argument("--" + name, required=name != "branch")
    p.add_argument("--details")
    p.add_argument("--commit-sha")
    p.add_argument("--started-at")
    p.add_argument("--completed-at")
    p.add_argument("--message-id")
    p.add_argument("--source", default="cloud-self-report")
    p.add_argument("--retries", type=int, default=3)
    return p


def report(args: argparse.Namespace) -> dict:
    if args.status not in STATUSES:
        raise ValueError("status must be one of: " + ", ".join(sorted(STATUSES)))
    url = os.environ.get("DOCADOX_GATEWAY_URL", "").rstrip("/") + "/v1/agent-events"
    secret = os.environ.get("DOCADOX_GATEWAY_ADAPTER_SECRET", "")
    if not url or not secret:
        raise RuntimeError("DOCADOX_GATEWAY_URL and DOCADOX_GATEWAY_ADAPTER_SECRET are required")
    completed_at = args.completed_at or datetime.now(timezone.utc).isoformat()
    message_id = args.message_id or "cloud-" + hashlib.sha256(
        "\x1f".join([args.provider, args.task_id, args.status, completed_at,
                     args.commit_sha or "", args.source]).encode()
    ).hexdigest()
    payload = {"provider": args.provider, "agent_id": args.agent_id, "task_id": args.task_id,
               "message_id": message_id, "status": args.status, "summary": args.summary,
               "details": args.details, "repository": args.repository, "branch": args.branch,
               "commit_sha": args.commit_sha, "started_at": args.started_at,
               "completed_at": completed_at, "source": args.source}
    body = json.dumps({k: v for k, v in payload.items() if v is not None}, separators=(",", ":")).encode()
    attempts = max(1, min(args.retries, 3))
    last_error = "callback_failed"
    for attempt in range(attempts):
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        signing = b"\n".join([timestamp.encode(), nonce.encode(), b"POST", b"/v1/agent-events", body])
        signature = hmac.new(secret.encode(), signing, hashlib.sha256).hexdigest()
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Content-Type": "application/json", "X-Reporter-Timestamp": timestamp,
            "X-Reporter-Nonce": nonce, "X-Reporter-Signature": signature})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read())
            return {"accepted": True, "message_id": message_id, "response": result}
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = type(exc).__name__
            if attempt + 1 < attempts:
                time.sleep(min(2 ** attempt, 4))
    raise RuntimeError(f"completion callback failed after {attempts} attempt(s): {last_error}")


def main(argv: list[str] | None = None) -> int:
    try:
        result = report(_parser().parse_args(argv))
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
