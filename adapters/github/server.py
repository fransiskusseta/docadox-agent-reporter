"""Standalone webhook/hook receiver for the GitHub Cloud Agent Adapter.

Deliberately a SEPARATE process/app from reporter/api.py: the reporter core
is loopback-only by design (its own middleware rejects any non-loopback
client, see reporter/api.py), which is correct for it -- but a GitHub
webhook or a Copilot Cloud HTTP hook is a real POST from GitHub's public
infrastructure and can never reach a loopback-only service directly. This
server is the one thing in this adapter that must be reachable from the
public internet; it does no more than verify a signature/shared secret,
parse a payload, and forward an already-normalized event to the reporter
(local or Gateway sink -- see adapter.py). It executes nothing from any
payload: no subprocess, no eval, no dynamic code path keyed on payload
content.

MUST be deployed behind the Owner's own TLS-terminating reverse proxy in any
real deployment -- see README.md "Security model". Binding 0.0.0.0 directly
to the internet with plain HTTP is not a supported configuration.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI, Header, HTTPException, Request
from starlette.responses import JSONResponse

from .adapter import GitHubCloudAdapter, WebhookSignatureInvalid
from .client import GitHubApiError
from .config import GitHubAdapterSettings
from .webhook import SUPPORTED_EVENTS

logger = logging.getLogger("github_adapter.server")


def create_app(*, adapter: GitHubCloudAdapter, settings: GitHubAdapterSettings) -> FastAPI:
    app = FastAPI(title="Docadox GitHub Cloud Agent Adapter", version="1.0")
    app.state.adapter = adapter

    @app.exception_handler(WebhookSignatureInvalid)
    async def _invalid_sig(_: Request, exc: WebhookSignatureInvalid):
        logger.warning("Rejected request: %s", exc)
        return JSONResponse(status_code=401, content={"error": str(exc)})

    @app.exception_handler(GitHubApiError)
    async def _github_error(_: Request, exc: GitHubApiError):
        logger.error("GitHub/sink API error while handling a webhook: %s", exc)
        return JSONResponse(status_code=502, content={"error": "upstream_error"})

    @app.post("/github/webhook")
    async def github_webhook(
        request: Request,
        x_github_event: str = Header(default=""),
        x_hub_signature_256: str | None = Header(default=None),
        x_github_delivery: str | None = Header(default=None),
    ):
        if x_github_event not in SUPPORTED_EVENTS:
            # Not signature-checked before this early return on purpose: an
            # event type we do not translate at all is safe to ignore
            # immediately, but every event we DO act on still always goes
            # through verify_signature below (no early-exit bypasses that).
            return {"ignored": True, "reason": "unsupported_event_type", "event": x_github_event}
        raw_body = await request.body()
        result = adapter.receive_webhook(
            event_type=x_github_event, signature_header=x_hub_signature_256,
            raw_body=raw_body, delivery_id=x_github_delivery,
        )
        return {"processed": result is not None, "result": result}

    @app.post("/github/copilot-hook")
    async def copilot_hook(
        request: Request,
        x_copilot_hook_event: str = Header(default=""),
        x_reporter_hook_secret: str | None = Header(default=None),
        x_copilot_delivery: str | None = Header(default=None),
    ):
        if not x_copilot_hook_event:
            raise HTTPException(status_code=422, detail="missing_x_copilot_hook_event_header")
        payload = await request.json()
        result = adapter.receive_copilot_hook(
            event_name=x_copilot_hook_event, secret_header=x_reporter_hook_secret,
            payload=payload, delivery_id=x_copilot_delivery,
        )
        return {"processed": result is not None, "result": result}

    @app.get("/health")
    def health():
        return {
            "status": "ok",
            "token_configured": settings.token_configured(),
            "webhook_secret_configured": bool(settings.webhook_secret),
            "copilot_hook_secret_configured": bool(settings.copilot_hook_secret),
            "sink": settings.sink,
        }

    return app
