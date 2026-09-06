"""Configuration for the GitHub Cloud Agent Adapter. Environment variables
only (mirrors reporter/config.py's own convention exactly, including reusing
its minimal .env loader so this package adds no new mandatory dependency for
configuration alone). No secret value is ever logged, printed, or returned
by any function in this adapter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = Path(os.environ.get("DOCADOX_REPORTER_DATA_DIR", ROOT_DIR / "data"))


def _load_dotenv(path: Path) -> None:
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
class GitHubAdapterSettings:
    # Auth: a single bearer token, supplied as EITHER a fine-grained personal
    # access token OR a manually-obtained GitHub App user-to-server access
    # token -- both are used identically (Authorization: Bearer <token>).
    # Deliberately NOT a classic GitHub App JWT + installation-token flow:
    # GitHub's own Agent Tasks API explicitly rejects installation tokens
    # (server-to-server auth) for this adapter's primary target, so
    # implementing that flow would not even work for the main use case. See
    # README.md "GitHub auth model".
    token: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_TOKEN", ""))

    # Standard GitHub webhook shared secret (HMAC-SHA256, X-Hub-Signature-256).
    webhook_secret: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_WEBHOOK_SECRET", ""))

    # Copilot Cloud HTTP hook shared secret. NOT a GitHub-issued cryptographic
    # signature -- GitHub's own hooks-reference documents no signing scheme
    # for these HTTP hooks at all. This is a custom header value the Owner
    # sets both here and in the target repo's own .github/hooks/*.json
    # `headers` field; verified by constant-time string comparison. See
    # README.md "Security model" for the honest limitation this implies.
    copilot_hook_secret: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GITHUB_COPILOT_HOOK_SECRET", ""))

    # Repositories to poll via the Agent Tasks API, "owner/repo" each.
    repos: tuple[str, ...] = field(default_factory=lambda: tuple(
        r.strip() for r in os.environ.get("DOCADOX_GITHUB_REPOS", "").split(",") if r.strip()
    ))

    # Which reporter agent_id slot this adapter instance reports as. Must be
    # one of reporter's own DOCADOX_REPORTER_AGENT_IDS allowlist (defaults to
    # "copilot-1", already present in that allowlist's own default value).
    agent_id: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_ADAPTER_AGENT_ID", "copilot-1"))
    agent_name: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GITHUB_ADAPTER_AGENT_NAME", "GitHub Copilot Cloud"))

    # Where normalized events are sent. "local" (default) posts directly to
    # the local reporter's own loopback API (DOCADOX_REPORTER_BASE_URL) --
    # zero extra infrastructure, full 10-status fidelity. "gateway" posts
    # HMAC-signed to Claude #1's Cloud Gateway /v1/cloud/events instead, for
    # a deployment where this adapter runs somewhere that cannot reach the
    # Owner's own loopback machine at all. Both sinks accept the same
    # ten-value canonical status contract.
    sink: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_ADAPTER_SINK", "local"))

    reporter_base_url: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_REPORTER_BASE_URL", "http://127.0.0.1:8787"))

    gateway_url: str = field(default_factory=lambda: os.environ.get("DOCADOX_GATEWAY_URL", "").rstrip("/"))
    gateway_adapter_secret: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GATEWAY_ADAPTER_SECRET", ""))

    poll_interval_sec: float = field(default_factory=lambda: float(
        os.environ.get("DOCADOX_GITHUB_ADAPTER_POLL_INTERVAL_SEC", "30")))
    max_backoff_sec: float = field(default_factory=lambda: float(
        os.environ.get("DOCADOX_GITHUB_ADAPTER_MAX_BACKOFF_SEC", "600")))

    state_db_path: Path = field(default_factory=lambda: Path(os.environ.get(
        "DOCADOX_GITHUB_ADAPTER_STATE_DB", str(DATA_DIR / "github_adapter.db"))))

    # Webhook/hook receiver server bind address. 0.0.0.0 by default because,
    # unlike the reporter core, this server MUST be reachable from GitHub's
    # infrastructure over the public internet -- see README.md "Security
    # model" for the mandatory reverse-proxy/TLS requirement this implies.
    host: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_ADAPTER_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: int(os.environ.get("DOCADOX_GITHUB_ADAPTER_PORT", "8790")))

    api_version: str = field(default_factory=lambda: os.environ.get("DOCADOX_GITHUB_API_VERSION", "2026-03-10"))
    api_base_url: str = field(default_factory=lambda: os.environ.get(
        "DOCADOX_GITHUB_API_BASE_URL", "https://api.github.com"))

    def token_configured(self) -> bool:
        return bool(self.token)

    def gateway_configured(self) -> bool:
        return bool(self.gateway_url and self.gateway_adapter_secret)


def get_settings() -> GitHubAdapterSettings:
    # Re-read every call, mirroring reporter/config.py's own rationale: tests
    # monkeypatch os.environ per-test without import-order fragility.
    return GitHubAdapterSettings()
