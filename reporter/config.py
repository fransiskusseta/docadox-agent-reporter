"""Configuration: everything is read from environment variables (optionally
loaded from a local .env file via a tiny built-in loader -- no third-party
dependency needed for that alone). Secrets are NEVER logged, printed, or
returned by any API/CLI surface in this package.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = Path.home() / ".docadox-reporter"
DATA_DIR = Path(os.environ.get("DOCADOX_REPORTER_DATA_DIR", DEFAULT_DATA_DIR))
DB_PATH = DATA_DIR / "reporter.db"


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (no override of already-set env vars), so this
    package has zero mandatory third-party dependencies for configuration
    alone. Never logs a line's value, only whether the file was found."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"').strip("'")


_load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    bot_token: str = field(default_factory=lambda: os.environ.get("DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN", ""))
    chat_id: str = field(default_factory=lambda: os.environ.get("DOCADOX_REPORTER_TELEGRAM_CHAT_ID", ""))
    host: str = field(default_factory=lambda: os.environ.get("DOCADOX_REPORTER_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(os.environ.get("DOCADOX_REPORTER_PORT", "8787")))
    poll_interval_sec: float = field(default_factory=lambda: float(
        os.environ.get("DOCADOX_REPORTER_POLL_INTERVAL_SEC", "3")))
    telegram_poll_mode: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_REPORTER_TELEGRAM_POLL_MODE",
        "cloud" if os.environ.get("DOCADOX_GATEWAY_URL") else "local"))
    allowed_agent_ids: frozenset[str] = field(default_factory=lambda: frozenset(
        a.strip() for a in os.environ.get(
            "DOCADOX_REPORTER_AGENT_IDS",
            "claude-1,claude-2,claude-3,claude-4,codex-1,codex-2,copilot-1,copilot-2",
        ).split(",") if a.strip()
    ))
    max_summary_len: int = 500
    max_details_len: int = 3500
    max_telegram_message_len: int = 4000  # Telegram's own hard cap is 4096

    def telegram_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def local_telegram_poller_enabled(self) -> bool:
        return self.telegram_poll_mode == "local"


def get_settings() -> Settings:
    # Re-read every call (not a frozen module-level singleton) so tests can
    # monkeypatch os.environ per-test without import-order fragility --
    # mirrors the same pattern this Owner's other codebase (Docadox itself)
    # already uses for its own test-env-var routing.
    return Settings()
