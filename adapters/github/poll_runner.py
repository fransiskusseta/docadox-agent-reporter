#!/usr/bin/env python3
"""Polling fallback runner for the GitHub Cloud Agent Adapter.

Where webhook coverage is incomplete -- and for the Agent Tasks API
specifically, since GitHub documents no webhook/event push for task/session
state changes at all (verified during this task; see README.md "Webhook
coverage") -- this is the PRIMARY way task state reaches the reporter, not
merely a fallback for that one API.

Usage:
    python -m adapters.github.poll_runner
    python -m adapters.github.poll_runner --once   # one poll cycle, for cron/systemd-timer use

Configurable via the same environment variables as the rest of this
adapter (see .env.example). Backs off exponentially (capped at
DOCADOX_GITHUB_ADAPTER_MAX_BACKOFF_SEC) on any GitHub API error, including
rate limiting, and never loses persisted state on a failure -- the next
successful poll resumes exactly where the last successful one left off
(adapters/github/state.py's own per-repo cursor).
"""
from __future__ import annotations

import argparse
import logging
import time

from .adapter import GitHubCloudAdapter
from .client import GitHubApiError, GitHubRateLimited
from .config import get_settings

logger = logging.getLogger("github_adapter.poll_runner")


def run_once(adapter: GitHubCloudAdapter) -> int:
    """Runs exactly one poll cycle and reports every changed task. Returns
    the number of events reported. Never raises for an individual event's
    own reporting failure (logged and skipped, matching the graceful-
    degradation contract every other part of this adapter follows) -- only
    a genuine GitHub API failure while polling propagates, so the caller's
    own backoff logic can react to it."""
    events = adapter.poll()
    reported = 0
    for event in events:
        try:
            adapter.report(event)
            reported += 1
        except GitHubApiError:
            logger.exception("Failed to report one normalized event to the sink; will not retry this one, "
                             "state already advanced past it (next poll will see the CURRENT state, not resend "
                             "this exact transition).")
    return reported


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="Run a single poll cycle and exit")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.repos:
        logger.error("DOCADOX_GITHUB_REPOS is empty -- nothing to poll. Set it to a comma-separated "
                     "owner/repo list and retry.")
        return
    if not settings.token_configured():
        logger.error("DOCADOX_GITHUB_TOKEN is not set -- cannot call the GitHub API. See README.md "
                     "'GitHub auth model'.")
        return

    adapter = GitHubCloudAdapter(settings)
    logger.info("GitHub Cloud Agent Adapter poller starting: repos=%s agent_id=%s sink=%s interval=%ss",
               ",".join(settings.repos), settings.agent_id, settings.sink, settings.poll_interval_sec)

    backoff = settings.poll_interval_sec
    while True:
        try:
            n = run_once(adapter)
            if n:
                logger.info("Reported %d changed task(s) this cycle.", n)
            backoff = settings.poll_interval_sec  # reset after a clean cycle
        except GitHubRateLimited as exc:
            wait = max(exc.retry_after_sec, settings.poll_interval_sec)
            logger.warning("Rate-limited by GitHub; waiting %.0fs before the next attempt.", wait)
            if args.once:
                return
            time.sleep(wait)
            continue
        except GitHubApiError:
            logger.exception("GitHub API error this poll cycle; backing off.")
            if args.once:
                return
            time.sleep(backoff)
            backoff = min(backoff * 2, settings.max_backoff_sec)
            continue

        if args.once:
            return
        time.sleep(settings.poll_interval_sec)


if __name__ == "__main__":
    main()
