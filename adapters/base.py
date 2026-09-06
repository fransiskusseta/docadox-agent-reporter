"""Agent adapter interface. See adapters/README.md."""
from __future__ import annotations

from typing import Protocol


class AgentAdapter(Protocol):
    def deliver(self, agent_id: str, message_text: str) -> bool:
        """Best-effort delivery into a live agent process. Returns True if
        delivery was attempted (not that the agent acted on it); False if
        this adapter cannot currently reach that agent. Never the sole
        record of an instruction having been handled -- see the inbox
        acknowledge endpoint/CLI command for that."""
        ...
