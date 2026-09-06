#!/usr/bin/env python3
"""Prototype tmux/stdin adapter for terminal-managed Claude/Codex sessions.
See adapters/README.md for the full explanation and its limits.

Usage:
    python adapters/tmux_adapter.py --agent claude-1 [--session claude-1] \
        [--base-url http://127.0.0.1:8787] [--interval 5]

Requires `tmux` on PATH. Never uses shell=True -- the message text is
passed as a single argv element to `tmux send-keys`, never interpolated
into a shell string, so there is no command-injection risk from an
attacker-controlled Owner-reply message.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request


class TmuxDeliveryError(Exception):
    pass


def tmux_session_exists(session: str) -> bool:
    result = subprocess.run(["tmux", "has-session", "-t", session],
                            capture_output=True, text=True)
    return result.returncode == 0


def deliver_to_tmux(session: str, message_text: str) -> bool:
    if not tmux_session_exists(session):
        return False
    subprocess.run(["tmux", "send-keys", "-t", session, message_text, "Enter"], check=True)
    return True


def fetch_unacknowledged(base_url: str, agent_id: str) -> list[dict]:
    url = f"{base_url}/v1/agents/{agent_id}/inbox?unacknowledged_only=true"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())["inbox"]


def main() -> None:
    parser = argparse.ArgumentParser(description="tmux delivery adapter prototype")
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session", default=None, help="tmux session name (default: same as --agent)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8787")
    parser.add_argument("--interval", type=float, default=5.0)
    parser.add_argument("--once", action="store_true", help="Deliver pending entries once and exit")
    args = parser.parse_args()
    session = args.session or args.agent

    delivered_ids: set[int] = set()
    while True:
        try:
            entries = fetch_unacknowledged(args.base_url, args.agent)
        except urllib.error.URLError as exc:
            print(f"Could not reach reporter API: {exc}", file=sys.stderr)
            entries = []
        for entry in entries:
            if entry["id"] in delivered_ids:
                continue
            ok = deliver_to_tmux(session, entry["message_text"])
            if ok:
                print(f"Delivered inbox #{entry['id']} to tmux session '{session}' "
                      f"(not yet acknowledged -- agent/human must ack explicitly).")
                delivered_ids.add(entry["id"])
            else:
                print(f"tmux session '{session}' not found; leaving inbox #{entry['id']} for polling.",
                     file=sys.stderr)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
