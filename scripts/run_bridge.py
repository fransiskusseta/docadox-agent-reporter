#!/usr/bin/env python3
"""Production-safe local Bridge launcher.

Non-secret defaults are supplied here; the optional 0600 env file is the only
place this launcher reads the Bridge secret from. Values are never printed.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path


ALLOWED = {
    "DOCADOX_GATEWAY_URL", "DOCADOX_BRIDGE_ID", "DOCADOX_BRIDGE_SECRET",
    "DOCADOX_REPORTER_DB_PATH", "DOCADOX_BRIDGE_STATE_PATH",
    "DOCADOX_BRIDGE_POLL_INTERVAL_SEC", "DOCADOX_BRIDGE_MAX_BACKOFF_SEC",
}


def _config_path() -> Path:
    config_home = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "docadox-reporter" / "bridge.env"


def _read_local_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    if path.is_symlink():
        raise RuntimeError("local Bridge config must not be a symlink")
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise RuntimeError("local Bridge config must be chmod 600")
    values: dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise RuntimeError(f"invalid local Bridge config line {number}")
        key, value = (part.strip() for part in line.split("=", 1))
        if key not in ALLOWED:
            raise RuntimeError(f"unsupported local Bridge config key {key}")
        values[key] = value.strip('"').strip("'")
    return values


def main() -> int:
    try:
        local = _read_local_env(_config_path())
    except (OSError, RuntimeError) as exc:
        print(f"Docadox Bridge configuration error: {exc}", file=sys.stderr)
        return 2

    root = Path(__file__).resolve().parents[1]
    data_dir = Path.home() / ".docadox-reporter"
    defaults = {
        "DOCADOX_GATEWAY_URL": "https://docadox-reporter-gateway.onrender.com",
        "DOCADOX_BRIDGE_ID": "bridge-1",
        "DOCADOX_REPORTER_DB_PATH": str(data_dir / "reporter.db"),
        "DOCADOX_BRIDGE_STATE_PATH": str(data_dir / "bridge.db"),
    }
    child_env = os.environ.copy()
    for key, value in {**defaults, **local}.items():
        child_env.setdefault(key, value)
    child_env["DOCADOX_GATEWAY_URL"] = local.get("DOCADOX_GATEWAY_URL", child_env["DOCADOX_GATEWAY_URL"])
    child_env["DOCADOX_BRIDGE_ID"] = local.get("DOCADOX_BRIDGE_ID", child_env["DOCADOX_BRIDGE_ID"])
    child_env["DOCADOX_REPORTER_DB_PATH"] = local.get("DOCADOX_REPORTER_DB_PATH", child_env["DOCADOX_REPORTER_DB_PATH"])
    child_env["DOCADOX_BRIDGE_STATE_PATH"] = local.get("DOCADOX_BRIDGE_STATE_PATH", child_env["DOCADOX_BRIDGE_STATE_PATH"])
    print(f"Docadox Bridge launching (id={child_env['DOCADOX_BRIDGE_ID']}, gateway={child_env['DOCADOX_GATEWAY_URL']})")
    return subprocess.call([sys.executable, "-m", "gateway.bridge"], cwd=root, env=child_env)


if __name__ == "__main__":
    raise SystemExit(main())
