from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

_LOCK = threading.Lock()
_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  message_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, agent_name TEXT,
  task_id TEXT NOT NULL, status TEXT NOT NULL, summary TEXT NOT NULL,
  details TEXT, client_timestamp TEXT, created_at TEXT NOT NULL,
  notified_at TEXT, last_error TEXT
);
CREATE TABLE IF NOT EXISTS instructions (
  message_id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, task_id TEXT,
  text TEXT NOT NULL, source_message_id TEXT, created_at TEXT NOT NULL,
  delivered_at TEXT, acknowledged_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE TABLE IF NOT EXISTS bridges (
  bridge_id TEXT PRIMARY KEY, last_seen_at TEXT NOT NULL,
  status TEXT NOT NULL, last_error TEXT
);
CREATE TABLE IF NOT EXISTS auth_nonces (
  nonce TEXT PRIMARY KEY, principal TEXT NOT NULL, expires_at INTEGER NOT NULL
);
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class GatewayStore:
    db_path: Path

    def __post_init__(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with _LOCK:
            conn = sqlite3.connect(self.db_path, timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def record_event(self, event: dict) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                (message_id,agent_id,agent_name,task_id,status,summary,details,client_timestamp,created_at)
                VALUES (?,?,?,?,?,?,?,?,?)""",
                (event["message_id"], event["agent_id"], event.get("agent_name"), event["task_id"],
                 event["status"], event["summary"], event.get("details"), event.get("timestamp"), now_iso()),
            )
            return cur.rowcount == 1

    def mark_event_notified(self, message_id: str, error: Optional[str] = None) -> None:
        with self._connect() as conn:
            if error is None:
                conn.execute("UPDATE events SET notified_at = ?, last_error = NULL WHERE message_id = ?",
                             (now_iso(), message_id))
            else:
                conn.execute("UPDATE events SET last_error = ? WHERE message_id = ?", (error[:500], message_id))

    def event_needs_notification(self, message_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute("SELECT notified_at FROM events WHERE message_id = ?", (message_id,)).fetchone()
            return row is not None and row["notified_at"] is None

    def enqueue_instruction(self, instruction: dict) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO instructions
                (message_id,agent_id,task_id,text,source_message_id,created_at)
                VALUES (?,?,?,?,?,?)""",
                (instruction["message_id"], instruction["agent_id"], instruction.get("task_id"),
                 instruction["text"], instruction.get("source_message_id"), now_iso()),
            )
            return cur.rowcount == 1

    def pending_instructions(self, limit: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM instructions WHERE acknowledged_at IS NULL ORDER BY created_at, message_id LIMIT ?",
                (limit,),
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE instructions SET delivered_at = ?, retry_count = retry_count + 1 WHERE message_id = ?",
                             (now_iso(), row["message_id"]))
            return rows

    def acknowledge_instruction(self, message_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE instructions SET acknowledged_at = ? WHERE message_id = ? AND acknowledged_at IS NULL",
                               (now_iso(), message_id))
            return cur.rowcount == 1

    def heartbeat(self, bridge_id: str, status: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO bridges(bridge_id,last_seen_at,status) VALUES(?,?,?) ON CONFLICT(bridge_id) DO UPDATE SET last_seen_at=excluded.last_seen_at,status=excluded.status,last_error=NULL",
                         (bridge_id, now_iso(), status))

    def register_nonce(self, nonce: str, principal: str, expires_at: int) -> bool:
        with self._connect() as conn:
            conn.execute("DELETE FROM auth_nonces WHERE expires_at < strftime('%s','now')")
            try:
                conn.execute("INSERT INTO auth_nonces(nonce,principal,expires_at) VALUES(?,?,?)",
                             (nonce, principal, expires_at))
            except sqlite3.IntegrityError:
                return False
            return True

    def status(self) -> dict:
        with self._connect() as conn:
            pending = conn.execute("SELECT COUNT(*) FROM instructions WHERE acknowledged_at IS NULL").fetchone()[0]
            bridges = [dict(row) for row in conn.execute("SELECT * FROM bridges ORDER BY bridge_id").fetchall()]
            return {"pending_instructions": pending, "bridges": bridges}
