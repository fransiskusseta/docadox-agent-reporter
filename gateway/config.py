from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _keys(value: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in value.split(","):
        if "=" not in item:
            continue
        name, secret = item.split("=", 1)
        if name.strip() and secret:
            result[name.strip()] = secret
    return result


@dataclass(frozen=True)
class GatewaySettings:
    db_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOCADOX_GATEWAY_DATA_DIR", "data")) / "gateway.db")
    host: str = field(default_factory=lambda: os.environ.get("DOCADOX_GATEWAY_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("DOCADOX_GATEWAY_PORT", "8788")))
    bridge_keys: dict[str, str] = field(default_factory=lambda: _keys(
        os.environ.get("DOCADOX_GATEWAY_BRIDGE_KEYS", "")))
    adapter_secret: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GATEWAY_ADAPTER_SECRET", ""))
    core_secret: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GATEWAY_CORE_SECRET", ""))
    request_skew_sec: int = field(default_factory=lambda: int(os.environ.get(
        "DOCADOX_GATEWAY_REQUEST_SKEW_SEC", "300")))
    nonce_ttl_sec: int = field(default_factory=lambda: int(os.environ.get(
        "DOCADOX_GATEWAY_NONCE_TTL_SEC", "600")))
    max_body_bytes: int = field(default_factory=lambda: int(os.environ.get(
        "DOCADOX_GATEWAY_MAX_BODY_BYTES", "65536")))
    rate_limit_per_minute: int = field(default_factory=lambda: int(os.environ.get(
        "DOCADOX_GATEWAY_RATE_LIMIT_PER_MINUTE", "120")))
    instruction_poll_limit: int = field(default_factory=lambda: int(os.environ.get(
        "DOCADOX_GATEWAY_INSTRUCTION_POLL_LIMIT", "50")))


@dataclass(frozen=True)
class BridgeSettings:
    gateway_url: str = field(default_factory=lambda: os.environ.get("DOCADOX_GATEWAY_URL", "").rstrip("/"))
    bridge_id: str = field(default_factory=lambda: os.environ.get("DOCADOX_BRIDGE_ID", ""))
    bridge_secret: str = field(default_factory=lambda: os.environ.get("DOCADOX_BRIDGE_SECRET", ""))
    reporter_db_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOCADOX_REPORTER_DB_PATH", "data/reporter.db")))
    state_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOCADOX_BRIDGE_STATE_PATH", "data/bridge.db")))
    poll_interval_sec: float = field(default_factory=lambda: float(os.environ.get(
        "DOCADOX_BRIDGE_POLL_INTERVAL_SEC", "10")))
    max_backoff_sec: float = field(default_factory=lambda: float(os.environ.get(
        "DOCADOX_BRIDGE_MAX_BACKOFF_SEC", "300")))
