# Agent adapters

v1 does **not** claim direct injection into every existing UI agent. The
reporter's core contract is: agents call the CLI/API to report events, and
Owner replies land in a durable per-agent inbox (`GET /v1/agents/{id}/inbox`
or `reporter_cli.py inbox --agent <id>`). That contract works for *any*
agent that can run a shell command or make an HTTP request, with zero
platform-specific integration.

An **adapter** is an optional, additional delivery mechanism that pushes an
already-routed inbox entry into a *live* agent process, instead of the agent
having to poll its own inbox. This directory defines the adapter interface
and ships one working prototype.

## Interface

```python
class AgentAdapter(Protocol):
    def deliver(self, agent_id: str, message_text: str) -> bool:
        """Attempt to deliver `message_text` to the live process for
        `agent_id`. Returns True if delivery was attempted successfully
        (not necessarily that the agent acted on it), False if this
        adapter cannot currently reach that agent (e.g. no matching tmux
        session) -- in which case the caller should leave the inbox entry
        unacknowledged for the agent to pick up on its own via polling."""
```

An adapter is best-effort and additive. It must never be the *only* path to
an instruction — the inbox is always the durable source of truth, and
`acknowledge()` is always what actually consumes an instruction. An adapter
that "delivers" text into a terminal is not the same as the agent having
processed it; only the agent calling `inbox --ack` means that.

## Shipped prototype: tmux/stdin adapter

`tmux_adapter.py` is a small, working prototype for terminal-managed
Claude/Codex sessions that run inside a named `tmux` session. It shells out
to `tmux send-keys` to type the instruction into that pane, followed by
Enter. This is genuinely useful for a human-supervised terminal agent loop,
and it is simple enough not to be brittle (it does not try to parse the
pane's own output, only write to it).

It requires:
- `tmux` installed and on `PATH`.
- The target agent's session named exactly `agent_id` (e.g. `tmux new -s
  claude-1 ...`), or pass an explicit `--session` override.

It is intentionally **not** wired into the poller automatically in v1 --
run it manually or from your own supervisor script, e.g.:

```bash
python adapters/tmux_adapter.py --agent claude-1 --session claude-1
```

This polls that agent's inbox and, for each unacknowledged entry, sends the
text into the named tmux pane. It does **not** acknowledge the entry itself
(delivery into a terminal is not confirmation the agent read/acted on it) --
the agent (or the human at the keyboard) should acknowledge via
`reporter_cli.py inbox --agent claude-1 --ack <id>` once it has genuinely
been handled.

## GitHub Copilot (VS Code chat)

**Known limitation, by design, not an oversight:** this v1 does **not**
attempt any GUI automation of the VS Code Copilot Chat panel (simulating
keystrokes/clicks into an Electron webview). That kind of automation is
brittle, breaks on every VS Code UI update, and is exactly the "overly
clever" territory the rest of this project deliberately avoids.

Copilot is fully **notification-capable and inbox-routed**: it can report
events via the CLI/API like any other agent, and Owner replies aimed at it
land in its own durable inbox (`copilot-1`/`copilot-2` in the default
allowlist) for the human operator to read out and paste into the Copilot
Chat panel by hand, or for a future, more robust VS Code extension-based
adapter (not GUI automation) to deliver directly. That extension is a safe
next step, not part of v1.

## GitHub Cloud Agent Adapter

`github/` is a separate, self-contained adapter for **cloud** (not local
VS Code) coding-agent work: GitHub Copilot's cloud agent (via its own
"Agent Tasks" REST API), plus generic compatibility for Claude/Codex used
as a GitHub third-party coding agent (via ordinary PR/check/workflow
events). It polls and receives real GitHub webhooks (verified
`X-Hub-Signature-256` HMAC signatures) and Copilot Cloud's own HTTP session
hooks (a shared secret, kept separate since GitHub documents no
cryptographic signature for those), normalizes everything into this
project's canonical event model, and reports through the same reporter
core `/v1/events` contract every other adapter/CLI caller uses (or,
optionally, through Claude #1's Cloud Gateway). Unlike the tmux prototype
above, it also has a genuine reverse-instruction implementation
(`deliver()`, this same `AgentAdapter` Protocol): a `@copilot`-mention
comment on the relevant PR/issue, GitHub's own documented way to hand the
agent more instructions.

See `github/README.md` for the full architecture, setup steps, required
GitHub permissions, and security model -- including the one narrow,
additive change this adapter required in the Reporter Core itself (the
status vocabulary was widened from 6 to this project's full 10 canonical
values; nothing existing was redesigned or renamed).
