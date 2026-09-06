"""Localhost-only Agent Event API.

`create_app()` is a factory (store/settings/telegram are injected) so tests
never need a real Telegram bot token or the default on-disk database path.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from starlette.responses import JSONResponse

from . import notifications
from .config import Settings
from .owner_gate_policy import ActionIntent, classify_action
from .store import Store
from .telegram import TelegramClient

_STATUS_VALUES = ("RUNNING", "PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "IDLE",
                  "QUEUED", "WAITING", "CANCELLED", "TIMED_OUT")


class EventIn(BaseModel):
    model_config = {"extra": "forbid"}

    agent_id: str = Field(min_length=1, max_length=64)
    agent_name: Optional[str] = Field(default=None, max_length=64)
    task_id: str = Field(min_length=1, max_length=128)
    status: str
    summary: str = Field(min_length=1, max_length=500)
    details: Optional[str] = Field(default=None, max_length=3500)
    timestamp: Optional[str] = Field(default=None, max_length=64)
    message_id: Optional[str] = Field(default=None, min_length=1, max_length=128)

    @field_validator("status")
    @classmethod
    def _status_must_be_canonical(cls, v: str) -> str:
        if v not in _STATUS_VALUES:
            raise ValueError(f"status must be one of {_STATUS_VALUES}")
        return v


class AckIn(BaseModel):
    model_config = {"extra": "forbid"}


def _client_is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else None
    return host in ("127.0.0.1", "::1", "testclient")


def create_app(*, store: Store, settings: Settings, telegram: Optional[TelegramClient]) -> FastAPI:
    app = FastAPI(title="Docadox Agent Reporter", version="1.0")
    app.state.store = store
    app.state.settings = settings
    app.state.telegram = telegram

    @app.middleware("http")
    async def _reject_non_loopback(request: Request, call_next):
        # Defense-in-depth beyond "bind to 127.0.0.1 by default": even if
        # something puts a proxy in front unexpectedly, this API refuses to
        # act on a request that did not originate from loopback.
        if not _client_is_loopback(request):
            return JSONResponse(status_code=403, content={"error": "loopback_only"})
        return await call_next(request)

    def _require_known_agent(agent_id: str) -> None:
        if agent_id not in settings.allowed_agent_ids:
            raise HTTPException(status_code=403, detail="unknown_agent_id")

    @app.post("/v1/events")
    def post_event(body: EventIn):
        _require_known_agent(body.agent_id)
        result = notifications.handle_event(
            store=store, settings=settings, telegram=telegram,
            agent_id=body.agent_id, agent_name=body.agent_name or body.agent_id,
            task_id=body.task_id, status=body.status, summary=body.summary,
            details=body.details, client_timestamp=body.timestamp,
            message_id=body.message_id,
        )
        return {
            "event_id": result.event_id,
            "notified": result.notified,
            "duplicate": result.duplicate,
            "message_id": result.message_id,
            "rejected_reason": result.rejected_reason,
        }

    @app.get("/v1/agents/{agent_id}/inbox")
    def get_inbox(agent_id: str, unacknowledged_only: bool = False):
        _require_known_agent(agent_id)
        rows = store.inbox_for(agent_id, unacknowledged_only=unacknowledged_only)
        return {"agent_id": agent_id, "inbox": [dict(r) for r in rows]}

    @app.post("/v1/agents/{agent_id}/inbox/{entry_id}/ack")
    def ack_inbox(agent_id: str, entry_id: int, _body: AckIn = AckIn()):
        _require_known_agent(agent_id)
        ok = store.acknowledge(agent_id, entry_id)
        if not ok:
            raise HTTPException(status_code=404, detail="inbox_entry_not_found_or_already_acknowledged")
        return {"agent_id": agent_id, "entry_id": entry_id, "acknowledged": True}

    @app.get("/v1/status")
    def get_status():
        rows = store.latest_state_per_agent()
        agents = []
        for row in rows:
            agents.append({
                "agent_id": row["agent_id"],
                "agent_name": row["agent_name"],
                "task": row["task_id"],
                "status": row["status"],
                "last_update": row["created_at"],
                "pending_owner_instructions": store.pending_instruction_count(row["agent_id"]),
            })
        return {"agents": agents, "configured_agent_ids": sorted(settings.allowed_agent_ids)}

    @app.get("/v1/health")
    def health():
        return {"status": "ok", "telegram_configured": settings.telegram_configured()}

    @app.post("/v1/owner-gate/classify")
    def classify_owner_gate(body: ActionIntent):
        """Classify structured action intent using the repo policy only."""
        return classify_action(body)

    return app
