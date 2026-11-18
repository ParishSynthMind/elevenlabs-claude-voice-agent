"""ElevenLabs post-call webhook signatures, and the shared secret on server-tool calls.

Post-call webhooks carry `ElevenLabs-Signature: t=<unix seconds>,v0=<hex>` where the hex is
HMAC-SHA256(webhook secret, "<t>.<raw body>"). This is the scheme ElevenLabs' own SDK checks
(`webhooks.construct_event`, elevenlabs 2.79.0); tests cross-check against that SDK.

We are stricter than the SDK in two ways: a timestamp too far in the FUTURE is refused too (the SDK
only checks the past), and every comparison is constant-time.

Server-tool (webhook tool) calls are not signed by ElevenLabs. They carry a header whose value comes
from an ElevenLabs workspace secret (so it never sits in the agent config in plain text), and we
compare it in constant time.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional

HEADER = "ElevenLabs-Signature"
TOOL_HEADER = "X-Agent-Tool-Secret"
TOLERANCE_SECONDS = 30 * 60  # the SDK's window


def sign(raw_body: str, secret: str, timestamp: Optional[int] = None) -> str:
    """Build a header value the way ElevenLabs does (used by tests and the local simulator)."""
    t = int(time.time()) if timestamp is None else int(timestamp)
    digest = hmac.new(secret.encode("utf-8"), f"{t}.{raw_body}".encode("utf-8"), hashlib.sha256).hexdigest()
    return f"t={t},v0={digest}"


def verify(raw_body: str, secret: Optional[str], header: Optional[str], now: Optional[float] = None,
           tolerance: int = TOLERANCE_SECONDS) -> bool:
    if not secret or not header:
        return False
    timestamp, candidates = None, []
    for part in header.split(","):
        part = part.strip()
        if part.startswith("t="):
            timestamp = part[2:]
        elif part.startswith("v0="):
            candidates.append(part[3:])
    if not timestamp or not candidates:
        return False
    try:
        t = int(timestamp)
    except ValueError:
        return False
    current = time.time() if now is None else now
    if abs(current - t) > tolerance:
        return False
    expected = hmac.new(secret.encode("utf-8"), f"{t}.{raw_body}".encode("utf-8"), hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, c) for c in candidates)


def tool_secret_ok(expected: Optional[str], given: Optional[str]) -> bool:
    if not expected or not given:
        return False
    return hmac.compare_digest(expected.encode("utf-8"), given.encode("utf-8"))
