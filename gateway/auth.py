from __future__ import annotations

import hashlib
import hmac
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from .config import GatewaySettings
from .store import GatewayStore


class RequestAuthenticator:
    def __init__(self, settings: GatewaySettings, store: GatewayStore) -> None:
        self.settings = settings
        self.store = store
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    async def verify(self, request: Request, secret: str, principal: str) -> None:
        if not secret:
            raise HTTPException(status_code=503, detail="authentication_not_configured")
        timestamp = request.headers.get("X-Reporter-Timestamp", "")
        nonce = request.headers.get("X-Reporter-Nonce", "")
        signature = request.headers.get("X-Reporter-Signature", "")
        try:
            ts = int(timestamp)
        except ValueError:
            raise HTTPException(status_code=401, detail="invalid_request_timestamp")
        if abs(int(time.time()) - ts) > self.settings.request_skew_sec:
            raise HTTPException(status_code=401, detail="stale_request")
        if len(nonce) < 16 or len(nonce) > 128 or len(signature) != 64:
            raise HTTPException(status_code=401, detail="invalid_request_authentication")
        body = await request.body()
        if len(body) > self.settings.max_body_bytes:
            raise HTTPException(status_code=413, detail="payload_too_large")
        expected = hmac.new(secret.encode(),
                            b"\n".join([timestamp.encode(), nonce.encode(), request.method.encode(),
                                       request.url.path.encode(), body]), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise HTTPException(status_code=401, detail="invalid_request_authentication")
        if not self.store.register_nonce(nonce, principal, ts + self.settings.nonce_ttl_sec):
            raise HTTPException(status_code=401, detail="replayed_request")
        now = time.time()
        bucket = self._requests[principal]
        while bucket and bucket[0] <= now - 60:
            bucket.popleft()
        if len(bucket) >= self.settings.rate_limit_per_minute:
            raise HTTPException(status_code=429, detail="rate_limit_exceeded")
        bucket.append(now)
