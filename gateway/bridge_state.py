from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class BridgeState:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivered_instructions (
              message_id TEXT PRIMARY KEY, local_entry_id INTEGER, acknowledged_at TEXT
            );
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def cursor(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM metadata WHERE key='event_cursor'").fetchone()
            return int(row[0]) if row else 0

    def set_cursor(self, value: int) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO metadata(key,value) VALUES('event_cursor',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (str(value),))

    def delivered(self, message_id: str) -> bool:
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM delivered_instructions WHERE message_id=?", (message_id,)).fetchone() is not None

    def remember_delivery(self, message_id: str, local_entry_id: int) -> None:
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO delivered_instructions(message_id,local_entry_id) VALUES(?,?)",
                         (message_id, local_entry_id))
