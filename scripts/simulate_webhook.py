#!/usr/bin/env python3
"""Replay a signed post-call webhook against a running server, the way ElevenLabs sends it.

  ELEVENLABS_WEBHOOK_SECRET=local-secret python scripts/simulate_webhook.py http://localhost:8000 examples/sample_post_call.json
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from voiceagent.signature import HEADER, sign  # noqa: E402


def main() -> int:
    base, payload_file = sys.argv[1], Path(sys.argv[2])
    secret = os.environ["ELEVENLABS_WEBHOOK_SECRET"]
    raw = payload_file.read_text(encoding="utf-8")
    r = httpx.post(base.rstrip("/") + "/webhooks/elevenlabs", content=raw,
                   headers={HEADER: sign(raw, secret), "Content-Type": "application/json"})
    print(r.status_code, r.text)
    r2 = httpx.post(base.rstrip("/") + "/webhooks/elevenlabs", content=raw,
                    headers={HEADER: sign(raw, "wrong-secret"), "Content-Type": "application/json"})
    print("forged:", r2.status_code)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
