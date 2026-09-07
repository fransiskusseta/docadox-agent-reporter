from pathlib import Path

from gateway.bridge import LocalBridge
from gateway.config import BridgeSettings
from reporter.config import Settings


def test_bridge_defaults_use_linux_local_storage(monkeypatch):
    for key in ("DOCADOX_REPORTER_DB_PATH", "DOCADOX_BRIDGE_STATE_PATH"):
        monkeypatch.delenv(key, raising=False)
    settings = BridgeSettings()
    assert settings.reporter_db_path == Path.home() / ".docadox-reporter" / "reporter.db"
    assert settings.state_path == Path.home() / ".docadox-reporter" / "bridge.db"


def test_bridge_creates_database_parent_directories(tmp_path):
    settings = BridgeSettings(
        gateway_url="http://gateway.test", bridge_id="bridge-1", bridge_secret="test-secret",
        reporter_db_path=tmp_path / "nested" / "reporter.db",
        state_path=tmp_path / "nested" / "bridge.db",
    )
    LocalBridge(settings)
    assert (tmp_path / "nested").is_dir()


def test_gateway_configuration_defaults_local_reporter_to_cloud_polling(monkeypatch):
    monkeypatch.setenv("DOCADOX_GATEWAY_URL", "https://gateway.test")
    monkeypatch.delenv("DOCADOX_REPORTER_TELEGRAM_POLL_MODE", raising=False)
    assert Settings().local_telegram_poller_enabled() is False


def test_local_polling_can_be_explicitly_enabled(monkeypatch):
    monkeypatch.setenv("DOCADOX_GATEWAY_URL", "https://gateway.test")
    monkeypatch.setenv("DOCADOX_REPORTER_TELEGRAM_POLL_MODE", "local")
    assert Settings().local_telegram_poller_enabled() is True
