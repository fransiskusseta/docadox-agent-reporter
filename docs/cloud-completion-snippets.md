# Cloud completion snippets

The Gateway has no reliable native Codex Cloud or Claude Code Web completion
webhook in this repository. Append one of these instructions to the task
prompt. The task's implementation result remains authoritative if the
notification callback is unavailable.

## Codex Cloud

```text
At terminal completion, report exactly once. This standalone callback runs from
the Docadox repository and needs no reporter source checkout:

```bash
python3 - <<'PY'
import hashlib, hmac, json, os, secrets, time, urllib.request
payload = {"provider":"codex", "agent_id":"codex-1", "task_id":"<stable-codex-task-id>", "message_id":"codex:<stable-task-id>:<terminal-attempt-id>", "status":"<PASS|BLOCKED|FAILED|OWNER_ACTION_REQUIRED|CANCELLED|TIMED_OUT>", "summary":"<concise final result>", "repository":"<owner/repository>", "branch":"<branch>", "commit_sha":"<sha-or-omit>", "source":"codex-cloud"}
body = json.dumps({k:v for k,v in payload.items() if not v.startswith("<")}, separators=(",", ":")).encode()
ts, nonce, path = str(int(time.time())), secrets.token_urlsafe(24), "/v1/agent-events"
sig = hmac.new(os.environ["DOCADOX_GATEWAY_ADAPTER_SECRET"].encode(), b"\n".join([ts.encode(), nonce.encode(), b"POST", path.encode(), body]), hashlib.sha256).hexdigest()
req = urllib.request.Request(os.environ["DOCADOX_GATEWAY_URL"].rstrip("/")+path, data=body, method="POST", headers={"Content-Type":"application/json","X-Reporter-Timestamp":ts,"X-Reporter-Nonce":nonce,"X-Reporter-Signature":sig})
print(urllib.request.urlopen(req, timeout=15).read().decode())
PY
```

Report even when there is no commit, PR, check, workflow, or push. Never print or expose Reporter credentials. Do not send duplicate completion events. If the callback fails after its bounded retries, keep the real implementation result and explicitly state the callback failure in the task output.
```

## Claude Cloud / Claude Code Web

```text
At terminal completion, report exactly once. This standalone callback runs from
the Docadox repository and needs no reporter source checkout:

```bash
python3 - <<'PY'
import hashlib, hmac, json, os, secrets, time, urllib.request
payload = {"provider":"claude", "agent_id":"claude-1", "task_id":"<stable-claude-task-id>", "message_id":"claude:<stable-task-id>:<terminal-attempt-id>", "status":"<PASS|BLOCKED|FAILED|OWNER_ACTION_REQUIRED|CANCELLED|TIMED_OUT>", "summary":"<concise final result>", "repository":"<owner/repository>", "branch":"<branch>", "commit_sha":"<sha-or-omit>", "source":"claude-cloud"}
body = json.dumps({k:v for k,v in payload.items() if not v.startswith("<")}, separators=(",", ":")).encode()
ts, nonce, path = str(int(time.time())), secrets.token_urlsafe(24), "/v1/agent-events"
sig = hmac.new(os.environ["DOCADOX_GATEWAY_ADAPTER_SECRET"].encode(), b"\n".join([ts.encode(), nonce.encode(), b"POST", path.encode(), body]), hashlib.sha256).hexdigest()
req = urllib.request.Request(os.environ["DOCADOX_GATEWAY_URL"].rstrip("/")+path, data=body, method="POST", headers={"Content-Type":"application/json","X-Reporter-Timestamp":ts,"X-Reporter-Nonce":nonce,"X-Reporter-Signature":sig})
print(urllib.request.urlopen(req, timeout=15).read().decode())
PY
```

Report even when there is no commit, PR, check, workflow, or push. Never print or expose Reporter credentials. Do not send duplicate completion events. If the callback fails after its bounded retries, keep the real implementation result and explicitly state the callback failure in the task output.
```
