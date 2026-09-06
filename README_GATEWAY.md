# Reporter v2 Cloud Gateway + Local Bridge

The Gateway is the always-on cloud component. It stores normalized agent events,
pending Owner instructions, delivery state, replay nonces, and bridge presence in
SQLite. It never executes Git, deployment, database, shell, or agent commands.

The Local Bridge runs on the Owner laptop and makes outbound HTTPS requests only.
It sends local Reporter events, polls pending instructions, writes them directly
to the existing Reporter Core SQLite inbox, and acknowledges them after the local
write succeeds. It does not start an HTTP server and does not expose a laptop port.

Cloud-originated actions use the same Reporter Core Owner-Gate policy as local
actions: `DOCADOX_REPORTER_OWNER_GATE_POLICY_V1`. Gateway and adapters must
forward structured action intent to `/v1/owner-gate/classify` or the shared
policy module; they must not define provider-specific gate rules. A normal
authorized commit, non-force push, merge, or reversible deployment remains
`ROUTINE`. Material governance, authority, lifecycle, scientific, security,
product, secret, destructive, irreversible, out-of-scope, or unresolved-risk
metadata produces `OWNER_GATE` and can use the existing
`OWNER_ACTION_REQUIRED` event status for notification.

## Components and contracts

```text
Cloud adapters ──HMAC──> Gateway /v1/cloud/events ──> Reporter Core notifier ──> Telegram
Reporter Core Telegram reply ──HMAC──> Gateway /v1/cloud/owner-replies
Local Reporter ── Local Bridge ──outbound HTTPS──> Gateway
Gateway queued instruction ──> Local Bridge ──> Reporter Core SQLite inbox
```

The Gateway uses the existing `reporter.notifications` formatter, secret guard,
and Telegram client through `ReporterCoreTelegramNotifier`. Telegram polling and
Owner allowlist policy remain Reporter Core responsibilities. The Core sends a
normalized `OwnerReply` to `/v1/cloud/owner-replies` after routing the reply to an
exact agent/task target.

In the cloud deployment, the Gateway also owns inbound Telegram polling. It uses
the same configured Owner chat, Telegram-message correlation, and secret guard,
but writes normalized replies directly to the Gateway's durable instruction
queue. The Local Bridge is then the only laptop component needed to deliver that
queue to the local Reporter inbox. In cloud mode, do not run the local Reporter
Telegram poller against the same bot; local-only development may continue to
use `python -m reporter` as the Telegram poller owner.

## Environment

Gateway:

```text
DOCADOX_GATEWAY_DATA_DIR=data
DOCADOX_GATEWAY_HOST=0.0.0.0
DOCADOX_GATEWAY_PORT=8788
DOCADOX_GATEWAY_BRIDGE_KEYS=bridge-1=<random-secret>
DOCADOX_GATEWAY_ADAPTER_SECRET=<random-secret>
DOCADOX_GATEWAY_CORE_SECRET=<random-secret>
DOCADOX_GATEWAY_REQUEST_SKEW_SEC=300
DOCADOX_GATEWAY_NONCE_TTL_SEC=600
DOCADOX_GATEWAY_MAX_BODY_BYTES=65536
DOCADOX_GATEWAY_RATE_LIMIT_PER_MINUTE=120
```

If Gateway-side Telegram notification is enabled, Reporter Core's existing
`DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN` and
`DOCADOX_REPORTER_TELEGRAM_CHAT_ID` variables are supplied through the cloud
service environment only. Values are never logged or returned.

Bridge:

```text
DOCADOX_GATEWAY_URL=https://gateway.example
DOCADOX_BRIDGE_ID=bridge-1
DOCADOX_BRIDGE_SECRET=<same-random-secret-as-the-gateway-key>
DOCADOX_REPORTER_DB_PATH=data/reporter.db
DOCADOX_BRIDGE_STATE_PATH=data/bridge.db
DOCADOX_BRIDGE_POLL_INTERVAL_SEC=10
DOCADOX_BRIDGE_MAX_BACKOFF_SEC=300
```

Generate and rotate secrets outside the application. Rotation is performed by
adding a new bridge key, restarting the bridge with the new secret, then removing
the old key. The current v2 implementation supports one active secret per bridge.

## Run locally

```bash
python -m gateway.service
python -m gateway.bridge
```

The Gateway may bind publicly in a cloud environment behind TLS. The Bridge must
only initiate outbound HTTPS. The local Reporter Core remains bound to loopback.

## Deployment options

The supplied `Dockerfile.gateway` is suitable for a small VPS or a managed
container service such as Render, Fly, or Railway. Configure TLS at the platform
edge and mount a durable volume at `DOCADOX_GATEWAY_DATA_DIR`; SQLite is suitable
for one Gateway instance in v2 development. A future PostgreSQL adapter can
replace `GatewayStore` while preserving the message and endpoint contracts.

## Failure and delivery behavior

Cloud events are inserted by immutable `message_id` before notification. Duplicate
events are accepted but do not notify twice after a successful notification. Owner
instructions are retained until an authenticated Bridge acknowledgement. Fetching
an instruction increments `retry_count`; lost acknowledgements cause safe replay.
The Bridge's local deterministic negative Telegram-style ID and state database make
local consumption idempotent across restart and sleep. Transient Gateway failures
use bounded exponential backoff.

Heartbeat state is persisted as `ONLINE`, `DEGRADED`, or `OFFLINE`. This service
does not send Telegram noise for heartbeat changes. Offline/pending-instruction
alerts can be added by the Reporter Core policy layer later.

## Security and limitations

All machine requests use HMAC-SHA256 over timestamp, nonce, method, path, and body.
Nonces are durable and single-use; stale requests, invalid signatures, wrong bridge
credentials, oversized bodies, and rate-limit excess are rejected. TLS is required
for cloud deployment. No endpoint executes commands, evaluates code, writes
arbitrary files, or exposes secrets. Payloads use strict Pydantic schemas.

SQLite is single-instance storage. Horizontal Gateway scaling, external KMS,
automatic key rotation, and heartbeat alert policy are future work. Telegram
inbound routing remains in Reporter Core; the Gateway accepts only its normalized
authenticated reply contract.
