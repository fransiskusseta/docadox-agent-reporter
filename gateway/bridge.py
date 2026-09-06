from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from reporter import approval
from reporter.store import Store

from .bridge_state import BridgeState
from .config import BridgeSettings


class BridgeTransportError(RuntimeError):
    pass


class GatewayClient:
    def __init__(self, settings: BridgeSettings) -> None:
        if not settings.gateway_url or not settings.bridge_id or not settings.bridge_secret:
            raise ValueError("DOCADOX_GATEWAY_URL, DOCADOX_BRIDGE_ID, and DOCADOX_BRIDGE_SECRET are required")
        self.settings = settings

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        body = b"" if method == "GET" else json.dumps(payload or {}, separators=(",", ":")).encode()
        timestamp = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        signing = b"\n".join([timestamp.encode(), nonce.encode(), method.encode(), path.encode(), body])
        signature = hmac.new(self.settings.bridge_secret.encode(), signing, hashlib.sha256).hexdigest()
        req = urllib.request.Request(
            self.settings.gateway_url + path, data=body if method != "GET" else None,
            headers={"Content-Type": "application/json", "X-Reporter-Bridge-Id": self.settings.bridge_id,
                     "X-Reporter-Timestamp": timestamp, "X-Reporter-Nonce": nonce,
                     "X-Reporter-Signature": signature}, method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                return json.loads(response.read())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise BridgeTransportError(type(exc).__name__) from exc

    def heartbeat(self) -> dict:
        return self._request("POST", "/v1/bridge/heartbeat", {"bridge_id": self.settings.bridge_id, "status": "ONLINE"})

    def event(self, event: dict) -> dict:
        return self._request("POST", "/v1/bridge/events", event)

    def instructions(self) -> list[dict]:
        return self._request("GET", "/v1/bridge/instructions").get("instructions", [])

    def acknowledge(self, message_id: str) -> dict:
        return self._request("POST", f"/v1/bridge/instructions/{message_id}/ack")


class LocalBridge:
    def __init__(self, settings: BridgeSettings) -> None:
        self.settings = settings
        self.client = GatewayClient(settings)
        self.state = BridgeState(settings.state_path)
        self.local_store = Store(settings.reporter_db_path)

    def _local_events(self) -> list[dict]:
        import sqlite3
        conn = sqlite3.connect(self.settings.reporter_db_path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 50", (self.state.cursor(),)).fetchall()
            return [dict(row) for row in rows]
        except sqlite3.OperationalError:
            return []
        finally:
            conn.close()

    @staticmethod
    def _event_payload(row: dict) -> dict:
        return {"message_id": f"local-{row['id']}", "agent_id": row["agent_id"],
                "agent_name": row.get("agent_name"), "task_id": row["task_id"],
                "status": row["status"], "summary": row["summary"],
                "details": row.get("details"), "timestamp": row.get("client_timestamp")}

    @staticmethod
    def _local_message_id(message_id: str) -> int:
        # Negative IDs cannot collide with Telegram's positive message IDs.
        return -int(hashlib.sha256(message_id.encode()).hexdigest()[:15], 16)

    def run_once(self) -> None:
        self.client.heartbeat()
        for row in self._local_events():
            self.client.event(self._event_payload(row))
            self.state.set_cursor(int(row["id"]))
        for item in self.client.instructions():
            message_id = item["message_id"]
            if not self.state.delivered(message_id):
                local_id = self.local_store.add_inbox_entry(
                    agent_id=item["agent_id"], task_id=item.get("task_id"),
                    message_text=item["text"], source_chat_id="cloud-gateway",
                    telegram_message_id=self._local_message_id(message_id),
                    reply_to_message_id=None,
                    contains_privileged_keyword=approval.contains_privileged_keyword(item["text"]),
                    routed=True,
                )
                self.state.remember_delivery(message_id, int(local_id or 0))
            self.client.acknowledge(message_id)


def run() -> None:
    import logging
    import time as time_module
    from .config import BridgeSettings

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    bridge = LocalBridge(BridgeSettings())
    backoff = 1.0
    while True:
        try:
            bridge.run_once()
            backoff = 1.0
            time_module.sleep(bridge.settings.poll_interval_sec)
        except (BridgeTransportError, OSError) as exc:
            logging.getLogger("docadox.bridge").warning("Gateway unavailable; retrying (%s)", type(exc).__name__)
            time_module.sleep(min(backoff, bridge.settings.max_backoff_sec))
            backoff = min(backoff * 2, bridge.settings.max_backoff_sec)


if __name__ == "__main__":
    run()
