from __future__ import annotations

from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Request
from starlette.responses import JSONResponse

from .auth import RequestAuthenticator
from .config import GatewaySettings
from .models import AgentEvent, Heartbeat, OwnerReply
from .notifier import EventNotifier, NullNotifier
from .store import GatewayStore


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

    async def core_auth(request: Request) -> None:
        await auth.verify(request, settings.core_secret, "reporter-core")

    @app.get("/v1/health")
    def health():
        return {"status": "ok"}

    @app.get("/v1/ready")
    def ready():
        # Opening the database is the readiness check; no external provider is required.
        store.status()
        return {"status": "ready", "storage": "sqlite"}

    @app.post("/v1/cloud/events")
    async def cloud_event(body: AgentEvent, _: None = Depends(adapter_auth)):
        event = body.model_dump()
        inserted = store.record_event(event)
        if inserted or store.event_needs_notification(body.message_id):
            notifier.notify(event, store)
        return {"message_id": body.message_id, "accepted": True, "duplicate": not inserted,
                "notification_pending": store.event_needs_notification(body.message_id)}

    @app.post("/v1/cloud/owner-replies")
    async def owner_reply(body: OwnerReply, _: None = Depends(core_auth)):
        inserted = store.enqueue_instruction(body.model_dump())
        return {"message_id": body.message_id, "accepted": True, "duplicate": not inserted}

    @app.post("/v1/bridge/events")
    async def bridge_event(body: AgentEvent, _: None = Depends(bridge_auth)):
        event = body.model_dump()
        inserted = store.record_event(event)
        if inserted or store.event_needs_notification(body.message_id):
            notifier.notify(event, store)
        return {"message_id": body.message_id, "accepted": True, "duplicate": not inserted}

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
