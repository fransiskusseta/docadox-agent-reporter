"""Outbound-message safety: control-character sanitization and a simple,
deliberately-not-clever secret-pattern guard. This is defense-in-depth, not
a substitute for callers never putting secrets in a summary/details field in
the first place -- it exists to catch the obvious, common cases (a pasted
Bearer token, an API key assignment, a password assignment, a raw .env
line) before anything reaches Telegram's servers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Deliberately simple, readable patterns -- "do not attempt overly clever
# secret scanning" per the task's own instruction. False positives (over-
# blocking) are an acceptable, safe failure mode here; false negatives on
# exotic secret shapes are not this guard's job.
_SECRET_PATTERNS = [
    re.compile(r"\bBearer\s+[A-Za-z0-9\-_.]{10,}", re.IGNORECASE),
    re.compile(r"\b(api[_-]?key|apikey)\s*[:=]\s*['\"]?[A-Za-z0-9\-_.]{8,}", re.IGNORECASE),
    re.compile(r"\bpassword\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\bsecret\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"\btoken\s*[:=]\s*['\"]?[A-Za-z0-9\-_.]{10,}", re.IGNORECASE),
    # A .env-style assignment of a long opaque value, e.g. FOO_KEY=sk-abcd1234...
    re.compile(r"^[A-Z][A-Z0-9_]{2,}\s*=\s*\S{12,}$", re.MULTILINE),
    # Common provider key prefixes, wherever they appear.
    re.compile(r"\bsk-[A-Za-z0-9]{10,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9\-]{10,}"),
    re.compile(r"\bkey_[A-Za-z0-9]{10,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{10,}"),
    # A raw JWT (three base64url segments separated by dots).
    re.compile(r"\beyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+\b"),
]

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class OutboundMessageRejected(Exception):
    """Raised when a message must not be sent (looks like it contains a
    secret). Callers must never retry with the same content unmodified."""


@dataclass(frozen=True)
class SanitizedMessage:
    text: str
    truncated: bool


def find_secret_like_pattern(text: str) -> str | None:
    """Returns a short, non-revealing label for the first pattern matched,
    or None. Never returns the matched substring itself."""
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return pattern.pattern[:40]
    return None


def sanitize_control_characters(text: str) -> str:
    return _CONTROL_CHAR_RE.sub("", text)


def prepare_outbound_text(text: str, max_len: int) -> SanitizedMessage:
    """Sanitizes and length-limits text destined for Telegram. Raises
    OutboundMessageRejected if an obvious secret pattern is present --
    callers must fix the content, never bypass this."""
    if find_secret_like_pattern(text) is not None:
        raise OutboundMessageRejected(
            "Outbound message blocked: it appears to contain a secret-shaped value "
            "(token/key/password/.env-style assignment/JWT). Remove it and retry."
        )
    cleaned = sanitize_control_characters(text)
    truncated = len(cleaned) > max_len
    if truncated:
        cleaned = cleaned[: max_len - 1].rstrip() + "…"
    return SanitizedMessage(text=cleaned, truncated=truncated)
