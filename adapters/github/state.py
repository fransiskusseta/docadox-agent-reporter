"""Durable local state for the GitHub Cloud Agent Adapter: last-known status
per (repo, task) for poll-level dedup, a per-repo polling cursor so a
restart resumes instead of re-scanning history, seen webhook/hook delivery
ids for redelivery dedup, and the last known open PR/issue number per agent
(for the reverse-instruction path -- deliver() needs to know WHERE to post a
comment).

Deliberately a SEPARATE sqlite file from reporter/store.py's reporter.db --
this adapter does not read or write reporter's own events/inbox tables at
all (see README.md "Adapter architecture" for why: this is state ABOUT
GitHub polling, not a second copy of reporter's own event log). No secret
value is ever stored here.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS task_state (
    repo TEXT NOT NULL,
    task_id TEXT NOT NULL,
    last_status TEXT NOT NULL,
    last_raw_state TEXT,
    pr_number INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (repo, task_id)
);

CREATE TABLE IF NOT EXISTS poll_cursor (
    repo TEXT PRIMARY KEY,
    since_timestamp TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_deliveries (
    delivery_id TEXT PRIMARY KEY,
    seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_target (
    agent_id TEXT PRIMARY KEY,
    repo TEXT NOT NULL,
    pr_number INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GitHubAdapterState:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with _lock:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    # ── task state (poll-level dedup) ───────────────────────────────────────
    def last_known_status(self, repo: str, task_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT last_status FROM task_state WHERE repo = ? AND task_id = ?", (repo, task_id)
            ).fetchone()
            return row["last_status"] if row else None

    def record_task_status(self, repo: str, task_id: str, status: str, raw_state: str,
                           pr_number: Optional[int]) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO task_state (repo, task_id, last_status, last_raw_state, pr_number, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(repo, task_id) DO UPDATE SET "
                "last_status=excluded.last_status, last_raw_state=excluded.last_raw_state, "
                "pr_number=excluded.pr_number, updated_at=excluded.updated_at",
                (repo, task_id, status, raw_state, pr_number, now_iso()),
            )

    # ── per-repo poll cursor ─────────────────────────────────────────────────
    def get_cursor(self, repo: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT since_timestamp FROM poll_cursor WHERE repo = ?", (repo,)).fetchone()
            return row["since_timestamp"] if row else None

    def set_cursor(self, repo: str, since_timestamp: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO poll_cursor (repo, since_timestamp) VALUES (?, ?) "
                "ON CONFLICT(repo) DO UPDATE SET since_timestamp = excluded.since_timestamp",
                (repo, since_timestamp),
            )

    # ── webhook/hook delivery dedup ──────────────────────────────────────────
    def mark_delivery_seen(self, delivery_id: str) -> bool:
        """Returns True if this is the FIRST time this delivery id has been
        seen (caller should process it); False if already seen (skip)."""
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO seen_deliveries (delivery_id, seen_at) VALUES (?, ?)",
                (delivery_id, now_iso()),
            )
            return cur.rowcount > 0

    # ── reverse-instruction target (deliver()) ──────────────────────────────
    def set_agent_target(self, agent_id: str, repo: str, pr_number: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO agent_target (agent_id, repo, pr_number, updated_at) VALUES (?,?,?,?) "
                "ON CONFLICT(agent_id) DO UPDATE SET repo=excluded.repo, pr_number=excluded.pr_number, "
                "updated_at=excluded.updated_at",
                (agent_id, repo, pr_number, now_iso()),
            )

    def get_agent_target(self, agent_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT repo, pr_number FROM agent_target WHERE agent_id = ?", (agent_id,)
            ).fetchone()
