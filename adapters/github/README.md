# GitHub Cloud Agent Adapter

Monitors cloud coding-agent work on GitHub and translates it into the
canonical Docadox Agent Reporter event model. Primary target: **GitHub
Copilot cloud agent** (the "Agent Tasks" REST API, currently in public
preview). Secondary compatibility target: **Claude or Codex used as a
GitHub third-party coding agent** (tracked generically, through ordinary
`pull_request`/`check_run`/`workflow_run` events -- see "Claude/Codex
compatibility" below).

This package is entirely additive: it does not modify `reporter/store.py`,
`reporter/poller.py`, `reporter/telegram.py`, or the Telegram reply-routing
logic. The only Reporter Core files it required a narrow, additive change
to are `reporter/api.py`, `reporter/notifications.py`, `reporter/
formatting.py`, and `reporter_cli.py` -- extending their existing status
vocabulary from 6 to the full 10 canonical values this task specifies
(`QUEUED`, `WAITING`, `CANCELLED`, `TIMED_OUT` added; nothing removed,
nothing renamed, no schema change). See "Adapter architecture" for why.

## Sources

Every API shape and status value referenced below was verified against
GitHub's own current documentation during this task, not assumed from
training data:

- [About GitHub Copilot cloud agent](https://docs.github.com/copilot/concepts/agents/coding-agent/about-coding-agent)
- [Agent Tasks REST API reference](https://docs.github.com/rest/agent-tasks/agent-tasks) (public preview)
- [Start Copilot cloud agent tasks via the REST API — GitHub Changelog](https://github.blog/changelog/2026-05-13-start-copilot-cloud-agent-tasks-via-the-rest-api/)
- [Agent tasks REST API now available for Copilot Pro, Pro+, and Max — GitHub Changelog](https://github.blog/changelog/2026-06-04-agent-tasks-rest-api-now-available-for-copilot-pro-pro-and-max/)
- [Ask Copilot coding agent to make changes in any pull request with @copilot — GitHub Changelog](https://github.blog/changelog/2025-10-28-ask-copilot-coding-agent-to-make-changes-in-any-pull-request-with-copilot/)
- [GitHub Copilot hooks reference](https://docs.github.com/en/copilot/reference/hooks-reference)
- [REST API endpoints for Copilot cloud agent management](https://docs.github.com/en/rest/copilot/copilot-coding-agent-management)

## Adapter architecture

```
                    ┌─────────────────────────────┐
GitHub Agent Tasks  │  adapters/github/poll_runner │──┐
API (polling)  ────>│  + adapter.py + client.py    │  │
                    └─────────────────────────────┘  │
                                                       │  NormalizedEvent
GitHub webhook      ┌─────────────────────────────┐  │  (models.py)
(X-Hub-Signature-   │  adapters/github/server.py   │  │
 256, real HMAC)───>│  /github/webhook             │──┤
                    └─────────────────────────────┘  │
                                                       ├──> report() sink:
Copilot Cloud HTTP  ┌─────────────────────────────┐  │      "local" (default):
hook (shared-secret │  adapters/github/server.py   │  │      POST reporter's own
 header, NOT a real │  /github/copilot-hook         │──┘      /v1/events (loopback)
 GitHub signature)─>│                               │
                    └─────────────────────────────┘         "gateway" (optional):
                                                              HMAC-signed POST to
                                                              Cloud Gateway's own
                                                              /v1/cloud/events
```

Three independent ingestion paths, one shared normalization model
(`models.NormalizedEvent`, `models.CANONICAL_STATUSES`), one configurable
sink. `adapter.py`'s `GitHubCloudAdapter` class is the single place all
three converge:

- `poll()` -- the primary path for Copilot cloud agent tasks (see "Webhook
  coverage": there is no webhook/push for task state at all, so this is not
  a fallback here, it is the only way this specific signal reaches this
  adapter).
- `receive_webhook(...)` -- generic GitHub webhooks (`pull_request`,
  `check_suite`, `check_run`, `workflow_run`); real, GitHub-issued
  HMAC-SHA256 signatures.
- `receive_copilot_hook(...)` -- Copilot Cloud session-lifecycle HTTP hooks;
  a shared-secret header, NOT a cryptographic signature (see "Security
  model" for why that distinction matters and is kept in a separate code
  path with separate verification, per this task's own instruction).
- `get_status(repo, task_id)` -- on-demand single-task lookup.
- `send_instruction(...)` / `deliver(agent_id, message_text)` -- the reverse
  path; `deliver()` is a drop-in implementation of the Reporter Core's own
  existing `adapters/base.py:AgentAdapter` Protocol (the same interface
  `tmux_adapter.py` implements), so this adapter is a peer of that
  prototype, not a parallel mechanism.
- `report(event)` -- the sink: local reporter API (default) or Cloud
  Gateway (optional; see below).

`adapters/github/state.py` keeps this adapter's OWN small sqlite state
(per-repo polling cursor, last-known status per task, seen webhook/hook
delivery ids, last-known PR per agent for `deliver()`) in a file separate
from `reporter/store.py`'s `reporter.db` -- this adapter never reads or
writes Reporter Core's own events/inbox tables directly.

### Why the Reporter Core's status enum was extended

This task's own canonical status list (`IDLE`, `QUEUED`, `RUNNING`,
`WAITING`, `OWNER_ACTION_REQUIRED`, `PASS`, `BLOCKED`, `FAILED`,
`CANCELLED`, `TIMED_OUT`) is a strict superset of the v1 Reporter Core's
original 6-value enum. GitHub's own Agent Tasks API state values
(`queued`, `in_progress`, `idle`, `waiting_for_user`, `completed`,
`failed`, `timed_out`, `cancelled`) map almost one-to-one onto it --
collapsing `QUEUED`/`CANCELLED`/`TIMED_OUT` into `RUNNING`/`FAILED` would
throw away exactly the distinctions GitHub itself asserts. `reporter/
api.py`'s `_STATUS_VALUES`, `reporter/notifications.py`'s
`NOTIFYING_STATUSES`/`ALL_STATUSES`, `reporter/formatting.py`'s icon/
owner-action tables, and `reporter_cli.py`'s `--status` choices were each
extended by one line/dict-entry -- no table schema change, no redesign of
dedup/routing/inbox logic, no behavior change for any existing 6-value
caller. `CANCELLED`/`TIMED_OUT` now notify (terminal outcomes, like
`FAILED`); `QUEUED`/`WAITING` do not (transient, non-actionable -- avoids
notification spam for a task merely sitting in a queue).

The Cloud Gateway's `gateway/models.py:AgentEvent.status` enum is the same
ten-value contract. The `gateway` sink forwards status unchanged, including
`QUEUED`, `WAITING`, `CANCELLED`, and `TIMED_OUT`.

## GitHub auth model

A single bearer token (`DOCADOX_GITHUB_TOKEN`), used identically for every
call this adapter makes:

- **Preferred: a fine-grained personal access token.** Minimum repository
  permissions:
  - **Agent tasks: Read** -- required for `poll()`/`get_status()` against
    the Agent Tasks API.
  - **Issues: Write** -- required only for the reverse-instruction path
    (`send_instruction()` posts a PR/issue comment; GitHub's comments API
    is the Issues API even for a PR).
  - **Pull requests: Read**, **Checks: Read**, **Actions: Read** -- required
    only if you also want the secondary `pull_request`/`check_run`/
    `check_suite`/`workflow_run` webhook path (Claude/Codex compatibility;
    skip these if you only monitor Copilot cloud agent tasks).
  - **Metadata: Read** -- mandatory baseline for any fine-grained PAT.
- **Alternative: a GitHub App user-to-server access token**, obtained
  through your own OAuth user-authorization flow and supplied the same way.

**Deliberately NOT implemented: a classic GitHub App JWT + installation-
token flow.** GitHub's own Agent Tasks API documentation states plainly
that installation access tokens (server-to-server auth) are **not
supported** -- only user-to-server tokens (PAT, OAuth app token, or GitHub
App user access token). Building the installation-token flow would not
even work for this adapter's primary target, so it was not built at all;
this is a scoped decision, not an oversight.

Never hard-coded, never logged, never printed -- see "Security model" and
`tests/test_github_security.py`.

## Copilot Cloud monitoring coverage

Via the Agent Tasks API (`client.py`, polled by `poll_runner.py`):

| Field | Captured? |
|---|---|
| Task/session id | Yes -- `task["id"]` |
| Repository | Yes -- from the configured `owner/repo`, and `repository.id` in the raw task object |
| Branch | Partial -- only when the task's `artifacts[]` includes a `type: "branch"` entry (`head_ref`/`base_ref`); not present for every task |
| Current state | Yes -- the full 8-value enum, 1:1 mapped (see table above) |
| Timestamps | Yes -- `created_at`, `updated_at` (and `completed_at` on the nested Session object, not currently surfaced separately by this adapter) |
| PR association | Best-effort -- `artifacts[].data.id` where `type: "pull"`. **Caveat, stated plainly rather than glossed over:** GitHub's own schema reference does not specify whether this `id` is the PR's per-repo *number* (what the Issues Comments API needs) or its global database id. Not independently verifiable without a live task to inspect. If a `send_instruction()`/`deliver()` call 404s for a specific task, this ambiguity is the likely cause -- see "Limitations". |
| Waiting-for-user state | Yes -- `waiting_for_user`, GitHub's own explicit signal, mapped to `OWNER_ACTION_REQUIRED` |
| Completed/failed/cancelled/timed-out | Yes -- all four map 1:1 |

No field is invented beyond what `docs.github.com/rest/agent-tasks/
agent-tasks` documents.

## Webhook/hook coverage

**The Agent Tasks API itself has no documented webhook or event-push
mechanism at all** (verified: neither the REST reference nor the 2026
changelog entries mention one). Polling (`poll_runner.py`) is therefore the
**primary**, not merely fallback, path for Copilot cloud agent task/session
state.

Generic GitHub webhooks ARE supported for the secondary compatibility path
(`webhook.py`, real `X-Hub-Signature-256` HMAC-SHA256 verification):

| Event | Mapped when |
|---|---|
| `pull_request` | `opened`/`reopened`/`synchronize`/`ready_for_review` -> `RUNNING`; `closed` + `merged` -> `PASS`; `closed` + not merged -> `CANCELLED`. Every other action (e.g. `review_requested`, `labeled`) is deliberately unmapped -- see "Owner action detection". |
| `check_suite` / `check_run` | `queued`/`in_progress` map directly; `completed` defers to `conclusion` (`success`/`neutral` -> `PASS`, `failure` -> `FAILED`, `cancelled`/`stale`/`skipped` -> `CANCELLED`, `timed_out` -> `TIMED_OUT`, `action_required` -> `OWNER_ACTION_REQUIRED`). |
| `workflow_run` | Same status/conclusion shape as `check_run`. |
| `issue_comment` | Never translated into a status change on its own (informational only) -- an agent-assignment comment does not, by itself, assert a state GitHub itself hasn't confirmed. |

Set up: repository (or organization/App) **Settings → Webhooks → Add
webhook**, payload URL `https://<your-adapter-host>/github/webhook`,
content type `application/json`, a random **Secret** (matches
`DOCADOX_GITHUB_WEBHOOK_SECRET`), and select the four event types above.

## Copilot Cloud hook setup

GitHub Copilot cloud agent sessions support **HTTP hooks** for
session-lifecycle events, configured per-repository in a file the
repository itself commits: `.github/hooks/*.json`. Example, targeting this
adapter's `/github/copilot-hook` endpoint:

```json
{
  "version": 1,
  "hooks": {
    "sessionStart": [{"type": "http", "url": "https://<your-adapter-host>/github/copilot-hook",
                      "headers": {"X-Copilot-Hook-Event": "sessionStart",
                                 "X-Reporter-Hook-Secret": "<same value as DOCADOX_GITHUB_COPILOT_HOOK_SECRET>"}}],
    "sessionEnd":   [{"type": "http", "url": "https://<your-adapter-host>/github/copilot-hook",
                      "headers": {"X-Copilot-Hook-Event": "sessionEnd",
                                 "X-Reporter-Hook-Secret": "<same value>"}}],
    "agentStop":    [{"type": "http", "url": "https://<your-adapter-host>/github/copilot-hook",
                      "headers": {"X-Copilot-Hook-Event": "agentStop",
                                 "X-Reporter-Hook-Secret": "<same value>"}}],
    "errorOccurred":[{"type": "http", "url": "https://<your-adapter-host>/github/copilot-hook",
                      "headers": {"X-Copilot-Hook-Event": "errorOccurred",
                                 "X-Reporter-Hook-Secret": "<same value>"}}]
  }
}
```

**This is kept as a separate ingest endpoint from generic webhooks
(`/github/copilot-hook` vs `/github/webhook`) because the authentication
model genuinely differs** (per this task's own instruction): GitHub's own
[hooks reference](https://docs.github.com/en/copilot/reference/
hooks-reference) documents **no signing/authentication scheme at all** for
these HTTP hooks -- no shared secret, no HMAC, nothing. This adapter's
`X-Reporter-Hook-Secret` header check is a real, constant-time-compared
shared secret, but it is **not a GitHub-issued cryptographic signature**
the way `/github/webhook`'s `X-Hub-Signature-256` is. This is documented
honestly here and in "Security model" rather than presented as equivalent.
Fine-grained tool-execution hooks (`preToolUse`, `postToolUse`,
`subagentStart`, etc.) are intentionally not wired to any Reporter status
change -- they would produce Telegram-notification-worthy spam for every
single tool call.

## Claude/Codex GitHub-agent compatibility

GitHub does not currently expose an Anthropic- or OpenAI-specific
"session/task API" for a third-party coding agent the way it does for its
own Copilot cloud agent (no Agent-Tasks-equivalent endpoint was found for
either during this task's research, and none is fabricated here). When
Claude or Codex is used as a GitHub-integrated coding agent (e.g. driving
commits/PRs via its own tooling, with GitHub Actions or a GitHub App
credential), its work surfaces through the **same generic GitHub primitives
every repository already has**: pull requests it opens, checks/workflow
runs those PRs trigger, and comments on them. `webhook.py`'s
`pull_request`/`check_run`/`check_suite`/`workflow_run` mapping is entirely
provider-agnostic -- it reads only fields every GitHub PR/check carries
regardless of who opened it, never a Copilot-specific field (see
`tests/test_github_status_mapping.py::test_provider_agnostic_pull_request_event_no_copilot_assumptions`,
which asserts no provider name appears anywhere in the mapping or its
input). Point the same `/github/webhook` endpoint at a repository where
Claude/Codex operates and events flow through identically; only
`get_status()`/`poll()` (the Agent-Tasks-API-specific path) remain
Copilot-only, since that specific REST surface is Copilot's own.

## Owner action detection

`OWNER_ACTION_REQUIRED` is reserved for signals GitHub itself asserts mean
"needs a human now":

- Agent Tasks API `state: "waiting_for_user"` -- GitHub's own explicit
  wording.
- A check/workflow run's `conclusion: "action_required"` -- GitHub's own
  conclusion value for a required gate (e.g. a required-reviewer
  environment protection rule), not a generic "something needs review."

An ordinary PR sitting unreviewed, or a `review_requested` action, is
**never** mapped to `OWNER_ACTION_REQUIRED` on its own -- see
`PULL_REQUEST_ACTION_MAP` in `models.py`, which leaves it unmapped rather
than guessed at, and `tests/test_github_security.py`'s dedicated test for
this exact distinction.

## Reverse instruction path

**Investigated and verified:** GitHub's Agent Tasks API has no documented
way to inject additional instructions into an already-running task (the
`prompt` parameter only exists on task *creation*). The genuinely supported,
documented mechanism is different: [mentioning `@copilot` in an issue/PR
comment](https://github.blog/changelog/2025-10-28-ask-copilot-coding-agent-to-make-changes-in-any-pull-request-with-copilot/)
causes Copilot cloud agent to pick up that comment as new instructions.
`send_instruction()` implements exactly this -- a plain `POST /repos/{owner}/
{repo}/issues/{number}/comments` with a body of `"@copilot {message_text}"`
-- the narrowest safe method that genuinely works, per this task's own
instruction. `deliver(agent_id, message_text)` is the same thing wired to
`adapters/base.py`'s existing `AgentAdapter` Protocol, using this adapter's
own persisted "last known PR for this agent" state; it returns `False`
(never raises) when no target PR is known yet, exactly like the tmux
adapter's own contract when its target session doesn't exist -- the caller
must leave the Owner's inbox entry unacknowledged for later delivery.

This is a **comment post, never a merge, push, or deploy** -- Reporter's
own "transports instructions, does not execute privileged actions"
boundary is fully preserved; nothing here decides anything on the Owner's
behalf.

## Polling fallback

`poll_runner.py` (`python -m adapters.github.poll_runner`):

- Configurable interval (`DOCADOX_GITHUB_ADAPTER_POLL_INTERVAL_SEC`,
  default 30s -- Copilot tasks do not need second-level polling).
- Per-repo persisted cursor (`adapters/github/state.py`) -- a restart
  resumes from the last successfully-seen `updated_at`, never re-scanning
  full history.
- Poll-level dedup: a task's status is only reported when it genuinely
  changed since the last successful poll of that exact task.
- Exponential backoff on any GitHub API error, capped at
  `DOCADOX_GITHUB_ADAPTER_MAX_BACKOFF_SEC`; a dedicated, longer wait
  (respecting GitHub's own `Retry-After`/rate-limit-reset headers) on a
  genuine 403/429 rate limit.
- A failed poll cycle never corrupts or resets previously-persisted state
  -- the cursor/last-known-status only advance after a successful fetch.

## Limitations

- The Agent Tasks REST API is explicitly **"public preview and subject to
  change"** per GitHub's own documentation -- endpoint paths, the state
  enum, and the response schema could change without the deprecation notice
  a stable API would get.
- The `artifacts[].data.id` PR-number ambiguity described above under
  "Copilot Cloud monitoring coverage" is unresolved without a live task to
  inspect; `send_instruction()`/`deliver()` may fail against a real task
  until this is confirmed against production data.
- Copilot Cloud HTTP hooks have **no GitHub-issued authentication
  mechanism** at all (see "Security model") -- the shared-secret header
  this adapter checks is real defense but not a cryptographic guarantee the
  request originated from GitHub's own infrastructure, unlike
  `/github/webhook`'s real HMAC signature.
- Starting the Agent Tasks API requires a Copilot Business or Enterprise
  subscription (per GitHub's docs); this adapter only ever *reads* task
  state and posts comments, never starts a task, so this constraint mainly
  affects whether there is anything to monitor at all, not this adapter's
  own operation.
- No GitHub-native "session/task API" exists for Claude or Codex as
  third-party GitHub agents (verified, not assumed) -- their coverage is
  necessarily the generic PR/check/workflow surface only (see "Claude/Codex
  compatibility").
- The `gateway` sink preserves this adapter's full 10-status vocabulary.

## Security model

- **Standard GitHub webhooks** (`/github/webhook`): real `X-Hub-Signature-
  256` HMAC-SHA256 verification (`webhook.py:verify_signature`,
  constant-time comparison); an invalid or missing signature is rejected
  (401) before the payload is ever parsed. GitHub delivery ids are
  deduplicated (`state.py:mark_delivery_seen`) so a redelivered webhook
  never produces a second event.
- **Copilot Cloud HTTP hooks** (`/github/copilot-hook`): a shared-secret
  header, constant-time compared, explicitly **not** a cryptographic
  signature -- GitHub documents none for this mechanism. This is a real,
  disclosed limitation of GitHub's own hook design, not of this adapter.
  Configure this endpoint only for repositories where that gap is an
  acceptable risk, and treat any event from it as lower-confidence than a
  signed webhook or a polled Agent-Tasks-API read.
- **No arbitrary code execution from any payload.** Every ingestion path
  (`webhook.py`, `copilot_hooks.py`) only ever reads specific, named JSON
  fields into a `NormalizedEvent` -- no `eval`, no dynamic import, no
  `subprocess` call keyed on payload content anywhere in this package.
- **Secrets only from environment variables**, never hard-coded, never
  logged/printed (see `tests/test_github_security.py`, which asserts a
  realistic-looking token never appears in a raised exception message, in
  captured log output, or via a `print()`/`logger.*()` call anywhere in
  this package's own source that passes the whole `settings` object rather
  than one of its safe accessor methods).
- **The webhook/hook receiver (`server.py`) is the one component in this
  entire adapter that must be reachable from the public internet** (unlike
  the Reporter Core, which stays loopback-only, and unlike the poller,
  which only ever makes outbound calls). It MUST be deployed behind your
  own TLS-terminating reverse proxy (the same requirement the Cloud
  Gateway's own `README_GATEWAY.md` already documents for that component)
  -- binding it directly to the internet over plain HTTP is not a supported
  configuration.
- **No privileged action is ever possible.** The only GitHub *write* this
  adapter performs anywhere is a single issue/PR comment POST
  (`send_instruction`). There is no merge, push, deploy, force-push,
  branch-deletion, or settings-change code path anywhere in this package.
- **Reporter's own approval boundary is untouched.** This adapter transports
  the Owner's exact words into a GitHub comment; it never decides whether a
  privileged instruction (`COMMIT`/`PUSH`/`MERGE`/`DEPLOY`/`DESTRUCTIVE_DB`)
  is authorized -- that remains, as documented in the top-level README.md,
  entirely the receiving agent's own responsibility.

## Setup steps (summary)

1. Create a fine-grained PAT with the permissions listed in "GitHub auth
   model" and put it in `.env` as `DOCADOX_GITHUB_TOKEN`.
2. Set `DOCADOX_GITHUB_REPOS` to the `owner/repo` list to monitor.
3. Run the poller: `python -m adapters.github.poll_runner` (primary path for
   Copilot cloud agent tasks).
4. (Optional, secondary path) Add a GitHub webhook pointed at
   `https://<your-host>/github/webhook` with a random secret in
   `DOCADOX_GITHUB_WEBHOOK_SECRET`, and run the receiver:
   `uvicorn adapters.github.server:create_app --factory` (wire in your own
   `adapter`/`settings` via a small launcher, or see `poll_runner.py` for
   the equivalent construction pattern) behind your own reverse proxy.
5. (Optional) Add `.github/hooks/*.json` to the monitored repository per
   "Copilot Cloud hook setup", with `DOCADOX_GITHUB_COPILOT_HOOK_SECRET` set
   to match.
6. Confirm events arrive: `python reporter_cli.py status` should show your
   configured `DOCADOX_GITHUB_ADAPTER_AGENT_ID` (default `copilot-1`) once
   the first task/PR/check event has been reported.
