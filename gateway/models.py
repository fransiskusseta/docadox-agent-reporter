from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

# One transport-neutral vocabulary shared with Reporter Core and the cloud
# adapters. Keep this tuple stable: persisted events are never remapped when
# they cross the Gateway boundary.
STATUS_VALUES = (
    "IDLE", "QUEUED", "RUNNING", "WAITING", "OWNER_ACTION_REQUIRED",
    "PASS", "BLOCKED", "FAILED", "CANCELLED", "TIMED_OUT",
)


class AgentEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: Optional[str] = Field(default=None, min_length=1, max_length=128)
    provider: Optional[str] = Field(default=None, max_length=64)
    agent_id: str = Field(min_length=1, max_length=64)
    agent_name: Optional[str] = Field(default=None, max_length=64)
    task_id: str = Field(min_length=1, max_length=128)
    status: str
    summary: str = Field(min_length=1, max_length=500)
    details: Optional[str] = Field(default=None, max_length=3500)
    timestamp: Optional[str] = Field(default=None, max_length=64)
    repository: Optional[str] = Field(default=None, max_length=256)
    branch: Optional[str] = Field(default=None, max_length=256)
    commit_sha: Optional[str] = Field(default=None, max_length=128)
    started_at: Optional[str] = Field(default=None, max_length=64)
    completed_at: Optional[str] = Field(default=None, max_length=64)
    source: Optional[str] = Field(default=None, max_length=64)

    @field_validator("status")
    @classmethod
    def canonical_status(cls, value: str) -> str:
        if value not in STATUS_VALUES:
            raise ValueError(f"status must be one of {STATUS_VALUES}")
        return value


class OwnerInstruction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: str = Field(min_length=1, max_length=128)
    agent_id: str = Field(min_length=1, max_length=64)
    task_id: Optional[str] = Field(default=None, max_length=128)
    text: str = Field(min_length=1, max_length=4000)
    source_message_id: Optional[str] = Field(default=None, max_length=128)


class Heartbeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bridge_id: str = Field(min_length=1, max_length=64)
    status: str = Field(default="ONLINE", pattern="^(ONLINE|DEGRADED|OFFLINE)$")


class OwnerReply(OwnerInstruction):
    """Reporter Core -> Gateway normalized Owner reply contract."""
