from __future__ import annotations

import hashlib
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from .auth import RequestAuthenticator
from .config import GatewaySettings
from .models import AgentEvent, Heartbeat, OwnerReply
from .notifier import EventNotifier, NullNotifier
from .store import GatewayStore
from reporter.security import find_secret_like_pattern

_TERMINAL_STATUSES = {"PASS", "BLOCKED", "FAILED", "OWNER_ACTION_REQUIRED", "CANCELLED", "TIMED_OUT"}


def _completion_message_id(body: AgentEvent) -> str:
    """Stable fallback identity for producers unable to provide an ID.

    Completion time/source/commit are completion identity when available;
    provider + task + terminal state remains the safe deterministic floor.
    """
    if body.message_id:
        return body.message_id
    identity = "\x1f".join([
        body.provider or "unknown", body.task_id, body.status,
        body.completed_at or body.timestamp or "", body.commit_sha or "",
        body.source or "cloud-self-report",
    ])
    return "cloud-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def create_app(*, store: GatewayStore, settings: GatewaySettings,
               notifier: Optional[EventNotifier] = None) -> FastAPI:
    app = FastAPI(title="Docadox Reporter Cloud Gateway", version="2.0")
    notifier = notifier or NullNotifier()
    auth = RequestAuthenticator(settings, store)
    app.state.store = store

    @app.middleware("http")
    async def payload_limit(request: Request, call_next):
        length = request.headers.get("content-length")
        if length and int(length) > settings.max_body_bytes:
            return JSONResponse(status_code=413, content={"error": "payload_too_large"})
        return await call_next(request)

    async def bridge_auth(request: Request) -> None:
        bridge_id = request.headers.get("X-Reporter-Bridge-Id", "")
        secret = settings.bridge_keys.get(bridge_id, "")
        await auth.verify(request, secret, f"bridge:{bridge_id}")
        request.state.bridge_id = bridge_id

    async def adapter_auth(request: Request) -> None:
        await auth.verify(request, settings.adapter_secret, "adapter")

    async def completion_auth(request: Request) -> None:
        # Completion producers use the already deployed adapter HMAC secret;
        # this avoids creating another privileged credential boundary.
        await auth.verify(request, settings.adapter_secret, "agent-completion")

    async def core_auth(request: Request) -> None:
        await auth.verify(request, settings.core_secret, "reporter-core")

    @app.get("/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/ready")
    def ready():
        # Opening the database is the storage check; cloud Telegram inbound
        # polling is also required when the production launcher attaches it.
        store.status()
        poller = getattr(app.state, "telegram_poller", None)
        if poller is not None and not poller.healthy:
            raise HTTPException(status_code=503, detail="telegram_inbound_unavailable")
        return {"status": "ready", "storage": "sqlite"}

    async def _record_event(body: AgentEvent, *, terminal_only: bool = False):
        if terminal_only and body.status not in _TERMINAL_STATUSES:
            raise HTTPException(status_code=422, detail="completion_status_must_be_terminal")
        for value in (body.summary, body.details or ""):
            if find_secret_like_pattern(value) is not None:
                raise HTTPException(status_code=422, detail="secret_like_content_rejected")
        event = body.model_dump()
        event["message_id"] = _completion_message_id(body)
        inserted = store.record_event(event)
        if inserted or store.event_needs_notification(event["message_id"]):
            notifier.notify(event, store)
        return {"message_id": event["message_id"], "accepted": True, "duplicate": not inserted,
                "notification_pending": store.event_needs_notification(event["message_id"])}

    @app.post("/v1/agent-events")
    async def agent_completion_event(body: AgentEvent, _: None = Depends(completion_auth)):
        return await _record_event(body, terminal_only=True)

    @app.post("/v1/cloud/events")
    async def cloud_event(body: AgentEvent, _: None = Depends(adapter_auth)):
        return await _record_event(body)

    @app.post("/v1/cloud/owner-replies")
    async def owner_reply(body: OwnerReply, _: None = Depends(core_auth)):
        inserted = store.enqueue_instruction(body.model_dump())
        return {"message_id": body.message_id, "accepted": True, "duplicate": not inserted}

    @app.post("/v1/bridge/events")
    async def bridge_event(body: AgentEvent, _: None = Depends(bridge_auth)):
        event = body.model_dump()
        if not event.get("message_id"):
            event["message_id"] = _completion_message_id(body)
        inserted = store.record_event(event)
        if inserted or store.event_needs_notification(event["message_id"]):
            notifier.notify(event, store)
        return {"message_id": event["message_id"], "accepted": True, "duplicate": not inserted}

    @app.get("/v1/bridge/instructions")
    async def instructions(_: None = Depends(bridge_auth)):
        rows = store.pending_instructions(settings.instruction_poll_limit)
        return {"instructions": [dict(row) for row in rows]}

    @app.post("/v1/bridge/instructions/{message_id}/ack")
    async def instruction_ack(message_id: str, _: None = Depends(bridge_auth)):
        if len(message_id) > 128:
            raise HTTPException(status_code=422, detail="message_id_too_long")
        acknowledged = store.acknowledge_instruction(message_id)
        return {"message_id": message_id, "acknowledged": acknowledged}

    @app.post("/v1/bridge/heartbeat")
    async def heartbeat(request: Request, body: Heartbeat, _: None = Depends(bridge_auth)):
        bridge_id = body.bridge_id
        header_id = getattr(request.state, "bridge_id", None)
        # The dependency authenticates the header; the body must still identify the same bridge.
        if header_id and bridge_id != header_id:
            raise HTTPException(status_code=403, detail="bridge_id_mismatch")
        store.heartbeat(bridge_id, body.status)
        return {"bridge_id": bridge_id, "status": body.status, "accepted": True}

    @app.get("/v1/status")
    def status():
        return store.status()

    return app
