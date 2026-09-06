# Docadox Agent Reporter v1

Up to 8 local coding agents each report meaningful status changes to a
single localhost service, which immediately relays the meaningful ones to
the Owner's Telegram. The Owner can reply from Telegram, and that reply is
routed back to the exact agent/task that needed it, landing in a durable
local inbox.

```
agents  →  local reporter (this project)  →  Telegram Bot  →  Owner phone
Owner Telegram reply  →  reporter  →  exact agent/task inbox
```

This project is a **separate, standalone project**. It does not modify, and
must never be run from inside, the Docadox MVP repository.

## Quick start

```bash
cd docadox-agent-reporter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env
# edit .env: set DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN and
# DOCADOX_REPORTER_TELEGRAM_CHAT_ID (see "Telegram setup" below)
.venv/bin/python -m reporter
```

The service binds to `127.0.0.1:8787` by default (loopback only) and starts
both the Agent Event API and the Telegram inbound-reply poller.

## Central Owner-Gate policy

Reporter uses one deterministic, repo-controlled policy versioned as
`DOCADOX_REPORTER_OWNER_GATE_POLICY_V1`. Agents classify structured action
metadata as either `ROUTINE` or `OWNER_GATE`; free-text descriptions help
explain an action but cannot downgrade it. Routine work continues
autonomously. A material governance, authority, lifecycle, scientific,
security, product, secret, destructive, irreversible, out-of-scope, or
ambiguous-risk action requires explicit Owner approval and may be reported as
`OWNER_ACTION_REQUIRED` through the normal event workflow.

Git and deployment commands are not gates by themselves. Ordinary authorized
staging, commits, non-force pushes, merges, development/staging deploys, and
reversible authorized production deploys classify as `ROUTINE` when no
material-risk flag is present. The loopback-only classifier is available from
the API at `POST /v1/owner-gate/classify` and from the CLI:

```bash
python reporter_cli.py classify-action --action-type TEST --environment LOCAL
python reporter_cli.py classify-action --action-type GIT_COMMIT --environment LOCAL
python reporter_cli.py classify-action --action-type GIT_PUSH --force-push-or-history-rewrite
```

The first two calls return `ROUTINE`; the third returns `OWNER_GATE`.
Malformed input and an unclassified `OTHER` action fail closed. Vague Owner
messages such as “ok” or “continue” do not resolve a specific outstanding
material gate.

## Reporting an event (CLI)

```bash
python reporter_cli.py report \
  --agent claude-1 \
  --task emergent-runtime \
  --status PASS \
  --summary "Emergent runtime contract complete"
```

Canonical statuses: `RUNNING`, `PASS`, `BLOCKED`, `FAILED`,
`OWNER_ACTION_REQUIRED`, `IDLE`, `QUEUED`, `WAITING`, `CANCELLED`,
`TIMED_OUT` (the last four added for the GitHub Cloud Agent Adapter --
see `adapters/github/README.md`). Only `PASS`/`BLOCKED`/`FAILED`/
`OWNER_ACTION_REQUIRED`/`CANCELLED`/`TIMED_OUT` trigger an immediate
Telegram notification. `RUNNING`/`IDLE`/`QUEUED`/`WAITING` are recorded
(visible in `status`) but never notify.

Repeating the exact same `agent + task + status + summary/details` does not
re-notify Telegram (deduplicated), though every event is still recorded.

## Reading/acknowledging an agent's inbox (CLI)

```bash
python reporter_cli.py inbox --agent claude-1
python reporter_cli.py inbox --agent claude-1 --ack 3
python reporter_cli.py status
```

An inbox entry is the **exact, literal text** the Owner sent from Telegram.
Acknowledging an entry (`--ack <id>`) is what marks it consumed; a
double-acknowledge is rejected (404), so an instruction is never processed
twice through this mechanism.

## Agent Event API (localhost only)

| Method | Path | Purpose |
|---|---|---|
| POST | `/v1/events` | Report a status event |
| GET | `/v1/agents/{agent_id}/inbox` | List an agent's inbox (optionally `?unacknowledged_only=true`) |
| POST | `/v1/agents/{agent_id}/inbox/{entry_id}/ack` | Acknowledge one inbox entry |
| GET | `/v1/status` | Latest status + pending-instruction count for every configured agent |
| GET | `/v1/health` | Liveness (no secret values ever returned) |

`POST /v1/events` body (strict schema, extra fields rejected):

```json
{
  "agent_id": "claude-1",
  "agent_name": "Claude #1",
  "task_id": "emergent-runtime",
  "status": "PASS",
  "summary": "Emergent runtime contract complete",
  "details": "optional, longer text",
  "timestamp": "optional, caller-supplied ISO timestamp"
}
```

Unknown `agent_id` values (not in the configured allowlist) are rejected
with `403`.

## Configuring 8 agents

Set `DOCADOX_REPORTER_AGENT_IDS` in `.env` to a comma-separated allowlist
(defaults shown):

```
DOCADOX_REPORTER_AGENT_IDS=claude-1,claude-2,claude-3,claude-4,codex-1,codex-2,copilot-1,copilot-2
```

Only agent ids in this list may report events or have an inbox. There is no
"register a new agent at runtime" endpoint — this is a deliberate, explicit
allowlist per the security requirements below.

## Telegram setup (steps the Owner must perform)

1. Open Telegram, message **@BotFather**, send `/newbot`, follow the
   prompts. BotFather gives you a bot token that looks like
   `123456789:AAExampleTokenDoNotShare`.
2. Start a chat with your new bot (search for its username and press
   Start), or add it to a private group you control.
3. Find your numeric chat id. The simplest way: send any message to the
   bot, then open
   `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a browser and
   read `message.chat.id` from the JSON response (do this once, from your
   own browser — never paste your token into a shared/public tool).
4. Put the token and chat id into your local `.env` (copied from
   `.env.example`) as `DOCADOX_REPORTER_TELEGRAM_BOT_TOKEN` and
   `DOCADOX_REPORTER_TELEGRAM_CHAT_ID`. Never commit `.env`.
5. Run `python -m reporter`. Report a test `PASS` event via the CLI and
   confirm the message arrives in your Telegram chat.
6. To test the reply path: reply directly to that Telegram message with any
   text, then run `python reporter_cli.py inbox --agent <that agent>` and
   confirm it appears.

If the bot token/chat id are not set, the service still runs (events are
recorded locally, visible via `status`), but nothing is ever sent to
Telegram — this is reported clearly via `rejected_reason:
"telegram_not_configured"` on the event response, never silently.

## Security model

- **Localhost bind by default** (`DOCADOX_REPORTER_HOST=127.0.0.1`), plus a
  defense-in-depth middleware inside the API itself that rejects any
  request whose connecting client is not loopback, regardless of bind
  configuration.
- **Explicit agent allowlist** — no unknown `agent_id` is ever accepted for
  either reporting events or reading/acknowledging an inbox.
- **Secrets only from environment variables.** The bot token and chat id
  are never printed, logged, returned by any API/CLI response, or written
  to the SQLite database. `.env` is gitignored; only `.env.example`
  (names/placeholders) is committed.
- **Outbound secret guard**: before anything is sent to Telegram, the
  composed message is checked against a small set of obvious secret-shaped
  patterns (Bearer tokens, `api_key=`/`password=`/`secret=`/`token=`
  assignments, `.env`-style `KEY=longvalue` lines, common provider key
  prefixes, raw JWTs). A match blocks the send entirely (`rejected_reason`
  is returned) rather than attempting to redact and send anyway. This is
  deliberately simple pattern matching, not a general secret scanner —
  callers must still avoid putting real secrets into `summary`/`details` in
  the first place.
- **Control-character sanitization** and a **length limit** (Telegram's own
  4096-character cap, we use 4000 as a safety margin) are applied to every
  outbound message.
- **No arbitrary shell-command execution endpoint. No `eval`.** Nothing in
  this codebase executes an inbound Telegram message as code or a shell
  command (see `tests/test_approval_safety.py`, which asserts the poller
  never calls `subprocess.run`/`Popen` for any inbound text, privileged
  keywords included).
- **No incoming Telegram attachments are ever processed** — only the plain
  `text` field of a message is read; photos/documents/etc. are ignored.
- **Telegram chat-id allowlist**: any inbound message from a chat other
  than the one configured chat id is discarded, never routed anywhere.
- **Persisted polling offset** (SQLite): a restart of the service never
  reprocesses old Telegram updates.

## Owner-approval safety (read this before wiring in a real agent)

This reporter is **messaging infrastructure, not an authorization engine**.
It never decides that a privileged action (`COMMIT`, `PUSH`, `MERGE`,
`DEPLOY`, `DESTRUCTIVE_DB`) may proceed, and it never executes one itself.

- Vague replies (`"ok"`, `"yes"`, `"continue"`, `"lanjut"`, etc.) are
  relayed to the inbox **completely unmodified**, with no elevated meaning
  attached. There is no code path anywhere in this project that treats a
  vague word as sufficient approval for a privileged action.
- When an Owner reply contains one of the privileged keywords, the
  **exact, literal text** is preserved in the inbox — never paraphrased,
  never summarized, never auto-flagged as "approved".
- It is the responsibility of the **receiving agent/orchestrator** — never
  this reporter — to decide whether a given inbox instruction is
  sufficient, authentic, and safe to act on. If an agent later reads
  `"APPROVE DEPLOY EMERGENT-MVP-001"` from its inbox, validating that
  decision remains entirely that agent's own job.

## Architecture / files

```
reporter/
  api.py            FastAPI app (localhost-only Agent Event API)
  approval.py       Privileged-keyword detection; NO "approved" concept lives here
  config.py         Env-var configuration (+ minimal built-in .env loader)
  formatting.py     Outbound Telegram message templates
  notifications.py  Event recording + dedup + send orchestration
  poller.py         Inbound Telegram getUpdates loop + reply routing
  security.py       Outbound secret-pattern guard + sanitization
  service.py        Runs API (uvicorn) + poller together, graceful shutdown
  store.py          SQLite persistence (events, inbox, telegram offset, message map)
  telegram.py       Minimal Telegram Bot API client (stdlib urllib only)
adapters/
  base.py           Adapter interface for future direct delivery into live agent processes
  tmux_adapter.py   Working prototype: pushes inbox text into a named tmux pane
  github/           GitHub Cloud Agent Adapter (Copilot cloud agent + Claude/Codex-as-
                    GitHub-agent compatibility) -- see adapters/github/README.md
  README.md         Explains adapter scope, including the Copilot limitation
reporter_cli.py     Standalone CLI (report / inbox / status)
tests/              Full suite against a fake Telegram client (no real bot token needed)
```

## Terminal status (v1 has no web dashboard)

```bash
python reporter_cli.py status
```

Shows every configured agent's latest status, task, last-update time, and
pending (unacknowledged) Owner-instruction count. No secret value is ever
included.

## Known limitations

- **GitHub Copilot (VS Code chat) cannot yet receive a directly-injected
  reply.** Copilot is fully notification-capable and inbox-routed like any
  other agent, but this v1 deliberately does not attempt GUI automation of
  the VS Code Copilot Chat panel (brittle, breaks on every UI update). See
  `adapters/README.md`.
- Reply routing without an explicit Telegram "reply to" falls back to the
  single agent currently in `OWNER_ACTION_REQUIRED`/`BLOCKED`, if there is
  exactly one; if there is more than one (or none), the message is stored
  under a special `unrouted` inbox rather than guessed at.
- The tmux adapter is a manually-run prototype, not wired into the poller
  automatically.
- No web dashboard; `status` is terminal/API only, as scoped for v1.

## Safe next step

Wire one real terminal-managed agent session (e.g. a `tmux`-hosted Claude
CLI session) to actually call `reporter_cli.py report` at its own natural
status-change points, and try the `adapters/tmux_adapter.py` prototype
end-to-end with a real bot token, before adding more agents.
Cloud smoke test
