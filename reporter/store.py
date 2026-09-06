"""Durable local state: SQLite (stdlib sqlite3, WAL mode for safe concurrent
access from the API process and the CLI at the same time). Holds: the event
log (for dedup + /v1/status), the Telegram message->agent/task map (for
reply routing), the per-agent inbox (Owner instructions), and the persisted
Telegram polling offset (to avoid replaying old updates after a restart).
No secret value is ever stored in any of these tables.
"""
from __future__ import annotations

import hashlib
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    agent_name TEXT,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    summary TEXT NOT NULL,
    details TEXT,
    dedup_key TEXT NOT NULL,
    message_id TEXT,
    content_blocked INTEGER NOT NULL DEFAULT 0,
    block_reason TEXT,
    notified INTEGER NOT NULL DEFAULT 0,
    client_timestamp TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_events_agent ON events(agent_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_events_dedup ON events(dedup_key);
CREATE UNIQUE INDEX IF NOT EXISTS ux_events_message_id
    ON events(message_id) WHERE message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS telegram_message_map (
    telegram_message_id INTEGER PRIMARY KEY,
    agent_id TEXT NOT NULL,
    task_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inbox (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL,
    task_id TEXT,
    message_text TEXT NOT NULL,
    source_chat_id TEXT NOT NULL,
    telegram_message_id INTEGER,
    reply_to_message_id INTEGER,
    contains_privileged_keyword INTEGER NOT NULL DEFAULT 0,
    routed INTEGER NOT NULL DEFAULT 1,
    received_at TEXT NOT NULL,
    acknowledged_at TEXT
);
CREATE INDEX IF NOT EXISTS ix_inbox_agent ON inbox(agent_id, received_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS ux_inbox_telegram_message
    ON inbox(telegram_message_id) WHERE telegram_message_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS telegram_offset (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    offset_value INTEGER NOT NULL
);
"""

_lock = threading.Lock()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def dedup_key_for(agent_id: str, task_id: str, status: str, summary: str, details: Optional[str]) -> str:
    raw = "\x1f".join([agent_id, task_id, status, summary, details or ""])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass
class Store:
    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
            self._migrate_add_message_id_column(conn)
            self._migrate_add_content_blocked_columns(conn)

    def _migrate_add_message_id_column(self, conn: sqlite3.Connection) -> None:
        """A database created before the message_id contract existed has an
        events table without that column; CREATE TABLE IF NOT EXISTS above is
        a no-op against it, so add the column here if it's missing."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "message_id" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN message_id TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_events_message_id "
            "ON events(message_id) WHERE message_id IS NOT NULL"
        )

    def _migrate_add_content_blocked_columns(self, conn: sqlite3.Connection) -> None:
        """A database created before the pre-persistence secret guard existed
        has an events table without these audit columns."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
        if "content_blocked" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN content_blocked INTEGER NOT NULL DEFAULT 0")
        if "block_reason" not in columns:
            conn.execute("ALTER TABLE events ADD COLUMN block_reason TEXT")

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

    # ── events ────────────────────────────────────────────────────────────
    def record_event(self, *, agent_id: str, agent_name: Optional[str], task_id: str, status: str,
                     summary: str, details: Optional[str], client_timestamp: Optional[str],
                     message_id: Optional[str] = None, content_blocked: bool = False,
                     block_reason: Optional[str] = None) -> tuple[int, bool]:
        """Inserts the event; returns (event_id, is_duplicate_of_a_prior_notified_event).
        The event is ALWAYS recorded (for the audit trail / /v1/status), even
        when it is a duplicate that must not re-notify.

        message_id is the canonical event-identity contract: a trusted
        producer (e.g. the gateway/bridge) may supply its own id, which is
        preserved exactly and used as the idempotency key -- resubmitting the
        same message_id never inserts a second row. It is treated as a
        duplicate (suppress re-notify) only once the existing row was already
        notified, so a retry after a genuinely failed send can still go
        through. A caller that omits message_id keeps the original
        content-hash dedup behavior unchanged (backward compatible).

        content_blocked/block_reason are pure audit metadata set by the
        caller (notifications.handle_event) -- this method never inspects
        summary/details for secrets itself; it persists exactly whatever
        summary/details it is given, so the caller is responsible for
        passing already-safe values when content_blocked=True."""
        with self._connect() as conn:
            if message_id is not None:
                existing = conn.execute(
                    "SELECT id, notified FROM events WHERE message_id = ? LIMIT 1", (message_id,)
                ).fetchone()
                if existing is not None:
                    return existing["id"], bool(existing["notified"])

            key = dedup_key_for(agent_id, task_id, status, summary, details)
            prior = conn.execute(
                "SELECT 1 FROM events WHERE dedup_key = ? AND notified = 1 LIMIT 1", (key,)
            ).fetchone()
            is_duplicate = prior is not None
            cur = conn.execute(
                "INSERT INTO events (agent_id, agent_name, task_id, status, summary, details, "
                "dedup_key, message_id, content_blocked, block_reason, notified, client_timestamp, "
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (agent_id, agent_name, task_id, status, summary, details, key, message_id,
                 int(content_blocked), block_reason, 0, client_timestamp, now_iso()),
            )
            return cur.lastrowid, is_duplicate

    def mark_notified(self, event_id: int) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE events SET notified = 1 WHERE id = ?", (event_id,))

    def latest_state_per_agent(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                """
                SELECT e.* FROM events e
                INNER JOIN (
                    SELECT agent_id, MAX(id) AS max_id FROM events GROUP BY agent_id
                ) latest ON latest.agent_id = e.agent_id AND latest.max_id = e.id
                ORDER BY e.agent_id
                """
            ).fetchall()

    # ── telegram message -> agent/task map (for reply routing) ─────────────
    def map_telegram_message(self, telegram_message_id: int, agent_id: str, task_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO telegram_message_map "
                "(telegram_message_id, agent_id, task_id, created_at) VALUES (?,?,?,?)",
                (telegram_message_id, agent_id, task_id, now_iso()),
            )

    def resolve_telegram_message(self, telegram_message_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM telegram_message_map WHERE telegram_message_id = ?",
                (telegram_message_id,),
            ).fetchone()

    def most_recent_action_required_agent(self) -> Optional[sqlite3.Row]:
        """Fallback routing target when an Owner reply is not a Telegram
        'reply to' a specific notification -- the single most recent
        OWNER_ACTION_REQUIRED/BLOCKED event across all agents, if exactly
        one agent is currently in that state. Ambiguous cases (more than
        one candidate, or none) are NOT guessed at; the caller must fall
        back to storing the message as unrouted for the Owner to clarify."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT e.* FROM events e
                INNER JOIN (
                    SELECT agent_id, MAX(id) AS max_id FROM events GROUP BY agent_id
                ) latest ON latest.agent_id = e.agent_id AND latest.max_id = e.id
                WHERE e.status IN ('OWNER_ACTION_REQUIRED', 'BLOCKED')
                """
            ).fetchall()
            return rows[0] if len(rows) == 1 else None

    # ── inbox ────────────────────────────────────────────────────────────
    def add_inbox_entry(self, *, agent_id: str, task_id: Optional[str], message_text: str,
                        source_chat_id: str, telegram_message_id: Optional[int],
                        reply_to_message_id: Optional[int], contains_privileged_keyword: bool,
                        routed: bool) -> int:
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO inbox (agent_id, task_id, message_text, source_chat_id, "
                "telegram_message_id, reply_to_message_id, contains_privileged_keyword, routed, "
                "received_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (agent_id, task_id, message_text, source_chat_id, telegram_message_id,
                 reply_to_message_id, int(contains_privileged_keyword), int(routed), now_iso()),
            )
            return cur.lastrowid

    def inbox_for(self, agent_id: str, *, unacknowledged_only: bool = False) -> list[sqlite3.Row]:
        query = "SELECT * FROM inbox WHERE agent_id = ?"
        if unacknowledged_only:
            query += " AND acknowledged_at IS NULL"
        query += " ORDER BY id ASC"
        with self._connect() as conn:
            return conn.execute(query, (agent_id,)).fetchall()

    def acknowledge(self, agent_id: str, entry_id: int) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE inbox SET acknowledged_at = ? WHERE id = ? AND agent_id = ? "
                "AND acknowledged_at IS NULL",
                (now_iso(), entry_id, agent_id),
            )
            return cur.rowcount > 0

    def pending_instruction_count(self, agent_id: str) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM inbox WHERE agent_id = ? AND acknowledged_at IS NULL",
                (agent_id,),
            ).fetchone()
            return row["n"]

    # ── telegram polling offset ─────────────────────────────────────────────
    def get_offset(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT offset_value FROM telegram_offset WHERE id = 1").fetchone()
            return row["offset_value"] if row else 0

    def set_offset(self, value: int) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO telegram_offset (id, offset_value) VALUES (1, ?) "
                "ON CONFLICT(id) DO UPDATE SET offset_value = excluded.offset_value",
                (value,),
            )
