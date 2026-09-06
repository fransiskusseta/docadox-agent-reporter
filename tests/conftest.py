import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient

from reporter.api import create_app
from reporter.config import Settings
from reporter.store import Store
from tests.fakes import FakeTelegramClient

TEST_CHAT_ID = "999888777"
TEST_AGENTS = frozenset({"claude-1", "claude-2", "codex-1"})


@pytest.fixture()
def store(tmp_path) -> Store:
    return Store(tmp_path / "reporter-test.db")


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        bot_token="fake-token-for-tests-only",
        chat_id=TEST_CHAT_ID,
        allowed_agent_ids=TEST_AGENTS,
    )


@pytest.fixture()
def telegram() -> FakeTelegramClient:
    return FakeTelegramClient()


@pytest.fixture()
def app(store, settings, telegram):
    return create_app(store=store, settings=settings, telegram=telegram)


@pytest.fixture()
def client(app) -> TestClient:
    return TestClient(app)
