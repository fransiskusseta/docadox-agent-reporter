# Cloud completion snippets

The Gateway has no reliable native Codex Cloud or Claude Code Web completion
webhook in this repository. Append one of these instructions to the task
prompt. The task's implementation result remains authoritative if the
notification callback is unavailable.

## Codex Cloud

```text
At terminal completion, report exactly once by running the repository helper:
python -m reporter.cloud_task_report --provider codex --agent-id codex-1 --task-id "<stable-codex-task-id>" --status "<PASS|BLOCKED|FAILED|OWNER_ACTION_REQUIRED|CANCELLED|TIMED_OUT>" --summary "<concise final result>" --repository "<owner/repository>" --branch "<branch>" --commit-sha "<sha-or-omit>" --message-id "codex:<stable-task-id>:<terminal-attempt-id>"

Report even when there is no commit, PR, check, workflow, or push. Never print or expose Reporter credentials. Do not send duplicate completion events. If the callback fails after its bounded retries, keep the real implementation result and explicitly state the callback failure in the task output.
```

## Claude Cloud / Claude Code Web

```text
At terminal completion, report exactly once by running the repository helper:
python -m reporter.cloud_task_report --provider claude --agent-id claude-1 --task-id "<stable-claude-task-id>" --status "<PASS|BLOCKED|FAILED|OWNER_ACTION_REQUIRED|CANCELLED|TIMED_OUT>" --summary "<concise final result>" --repository "<owner/repository>" --branch "<branch>" --commit-sha "<sha-or-omit>" --message-id "claude:<stable-task-id>:<terminal-attempt-id>"

Report even when there is no commit, PR, check, workflow, or push. Never print or expose Reporter credentials. Do not send duplicate completion events. If the callback fails after its bounded retries, keep the real implementation result and explicitly state the callback failure in the task output.
```
