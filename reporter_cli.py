#!/usr/bin/env python3
"""Docadox Agent Reporter CLI -- talks to the local reporter API only
(stdlib urllib, no extra dependency). Never reads or prints a Telegram bot
token or chat ID; those live only inside the running reporter service.

Usage:
    python reporter_cli.py report --agent claude-1 --task emergent-runtime \
        --status PASS --summary "Emergent runtime contract complete"

    python reporter_cli.py inbox --agent claude-1
    python reporter_cli.py inbox --agent claude-1 --ack 3
    python reporter_cli.py status
    python reporter_cli.py classify-action --action-type TEST --environment LOCAL
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

from reporter.owner_gate_policy import ActionType

DEFAULT_BASE_URL = os.environ.get("DOCADOX_REPORTER_BASE_URL", "http://127.0.0.1:8787")


def _request(method: str, path: str, base_url: str, body: dict | None = None) -> dict:
    url = f"{base_url}{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {"error": exc.reason}
        print(f"ERROR ({exc.code}): {json.dumps(detail)}", file=sys.stderr)
        sys.exit(1)
    except urllib.error.URLError as exc:
        print(f"ERROR: could not reach reporter at {base_url} ({exc.reason}). "
              f"Is the service running (python -m reporter)?", file=sys.stderr)
        sys.exit(1)


def cmd_report(args: argparse.Namespace) -> None:
    body = {
        "agent_id": args.agent,
        "agent_name": args.agent_name or args.agent,
        "task_id": args.task,
        "status": args.status,
        "summary": args.summary,
    }
    if args.details:
        body["details"] = args.details
    result = _request("POST", "/v1/events", args.base_url, body)
    print(json.dumps(result, indent=2))


def cmd_inbox(args: argparse.Namespace) -> None:
    if args.ack is not None:
        result = _request("POST", f"/v1/agents/{args.agent}/inbox/{args.ack}/ack", args.base_url, {})
        print(json.dumps(result, indent=2))
        return
    qs = "?unacknowledged_only=true" if args.unacknowledged_only else ""
    result = _request("GET", f"/v1/agents/{args.agent}/inbox{qs}", args.base_url)
    print(json.dumps(result, indent=2))


def cmd_status(args: argparse.Namespace) -> None:
    result = _request("GET", "/v1/status", args.base_url)
    print(json.dumps(result, indent=2))


def cmd_classify_action(args: argparse.Namespace) -> None:
    body = {"action_type": args.action_type, "target_environment": args.environment}
    if args.out_of_scope:
        body["authorized_scope"] = False
    for name in (
        "destructive", "irreversible", "force_push_or_history_rewrite",
        "changes_frozen_governance", "changes_authority", "changes_lifecycle_semantics",
        "changes_scientific_semantics", "material_security_boundary_change",
        "unresolved_material_product_choice", "requires_owner_secret_action",
        "material_risk_unknown",
    ):
        if getattr(args, name):
            body[name] = True
    if args.description:
        body["description"] = args.description
    result = _request("POST", "/v1/owner-gate/classify", args.base_url, body)
    print(json.dumps(result, indent=2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Docadox Agent Reporter CLI")
    parser.add_argument("--base-url", dest="base_url", default=DEFAULT_BASE_URL,
                        help="Reporter API base URL (default: %(default)s)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_report = sub.add_parser("report", help="Report a status event for an agent/task")
    p_report.add_argument("--agent", required=True)
    p_report.add_argument("--agent-name", dest="agent_name", default=None)
    p_report.add_argument("--task", required=True)
    p_report.add_argument("--status", required=True,
                          choices=["RUNNING", "PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "IDLE",
                                  "QUEUED", "WAITING", "CANCELLED", "TIMED_OUT"])
    p_report.add_argument("--summary", required=True)
    p_report.add_argument("--details", default=None)
    p_report.set_defaults(func=cmd_report)

    p_inbox = sub.add_parser("inbox", help="View or acknowledge an agent's inbox")
    p_inbox.add_argument("--agent", required=True)
    p_inbox.add_argument("--unacknowledged-only", dest="unacknowledged_only", action="store_true")
    p_inbox.add_argument("--ack", type=int, default=None, metavar="ENTRY_ID",
                         help="Acknowledge one inbox entry by id instead of listing")
    p_inbox.set_defaults(func=cmd_inbox)

    p_status = sub.add_parser("status", help="Show all agents' latest status")
    p_status.set_defaults(func=cmd_status)

    p_classify = sub.add_parser("classify-action", help="Classify an action as ROUTINE or OWNER_GATE")
    p_classify.add_argument("--action-type", required=True,
                            choices=[action.value for action in ActionType])
    p_classify.add_argument("--environment", choices=["LOCAL", "DISPOSABLE", "NON_PRODUCTION", "PRODUCTION"],
                            default="LOCAL")
    p_classify.add_argument("--out-of-scope", action="store_true")
    p_classify.add_argument("--destructive", action="store_true")
    p_classify.add_argument("--irreversible", action="store_true")
    p_classify.add_argument("--force-push-or-history-rewrite", dest="force_push_or_history_rewrite", action="store_true")
    p_classify.add_argument("--changes-frozen-governance", action="store_true")
    p_classify.add_argument("--changes-authority", action="store_true")
    p_classify.add_argument("--changes-lifecycle-semantics", action="store_true")
    p_classify.add_argument("--changes-scientific-semantics", action="store_true")
    p_classify.add_argument("--material-security-boundary-change", action="store_true")
    p_classify.add_argument("--unresolved-material-product-choice", action="store_true")
    p_classify.add_argument("--requires-owner-secret-action", action="store_true")
    p_classify.add_argument("--material-risk-unknown", action="store_true")
    p_classify.add_argument("--description", default=None)
    p_classify.set_defaults(func=cmd_classify_action)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
