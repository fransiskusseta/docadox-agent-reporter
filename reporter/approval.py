"""Owner-approval safety classification.

This module NEVER produces an "approved" boolean, and NEVER decides that an
instruction may proceed. Its only job is to flag, for visibility, whether an
inbound Owner reply text contains a privileged-action keyword -- so a human
or the receiving agent/orchestrator can see the flag and apply its OWN
judgement. The reporter is messaging infrastructure, not an authorization
engine, and this module is the one place that boundary is enforced in code:
there is deliberately no function anywhere in this package that returns
"approved" or "authorized".

Vague words ("ok", "yes", "continue", "lanjut", "go", "sure", "done") are
NEVER treated as sufficient to satisfy a privileged action on their own --
they are relayed to the inbox completely unmodified, exactly like every
other message, with no elevated flag attached.
"""
from __future__ import annotations

import re

PRIVILEGED_KEYWORDS = ("COMMIT", "PUSH", "MERGE", "DEPLOY", "DESTRUCTIVE_DB")

_PRIVILEGED_RE = re.compile(
    r"\b(" + "|".join(re.escape(k) for k in PRIVILEGED_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Deliberately NOT used to grant anything -- see module docstring. Kept only
# so a CLI/display layer can show "(this reply is a short, vague word -- if
# it is meant to approve a privileged action, the Owner should be asked to
# restate it explicitly)" without the reporter itself deciding anything.
_VAGUE_WORDS = {"ok", "okay", "yes", "y", "continue", "lanjut", "go", "sure", "done", "proceed"}


def contains_privileged_keyword(text: str) -> bool:
    return bool(_PRIVILEGED_RE.search(text or ""))


def is_vague_only(text: str) -> bool:
    """True only when the ENTIRE message (after trimming/lowering) is one
    of the known vague acknowledgement words -- never true for a longer
    message that merely contains one of these words among other content."""
    normalized = (text or "").strip().strip(".!").lower()
    return normalized in _VAGUE_WORDS
