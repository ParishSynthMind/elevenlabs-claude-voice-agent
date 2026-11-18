#!/usr/bin/env python3
"""Run the whole server path offline, no ElevenLabs account and no credits: the four server tools the
agent calls (with and without the shared secret), a signed post-call webhook, a forged one, an
ElevenLabs retry of the same webhook, and the cost guard tripping. Writes examples/local-run/run.json.

    python scripts/local_run.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from voiceagent.app import build_tools, create_app  # noqa: E402
from voiceagent.config import Settings  # noqa: E402
from voiceagent.signature import HEADER, TOOL_HEADER, sign  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TZ = "Europe/London"
NOW = datetime(2026, 11, 17, 9, 0, tzinfo=ZoneInfo(TZ))  # a Tuesday morning, fixed so the run repeats exactly


def main() -> int:
    steps = []
    alerts = []
    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp), tool_secret="local-tool-secret", webhook_secret="local-webhook-secret",
                            admin_token="local-admin", timezone=TZ, daily_credit_budget=1000, daily_call_limit=10)
        tools = build_tools(settings, alert=lambda s, d: alerts.append({"subject": s, "detail": d}))
        tools.scheduler.now = lambda: NOW
        tools.guard._now = lambda: NOW
        client = TestClient(create_app(settings, tools))
        admin = {"Authorization": "Bearer local-admin"}

        def tool(name, args, secret="local-tool-secret"):
            headers = {TOOL_HEADER: secret} if secret else {}
            r = client.post(f"/tools/{name}", json={**args, "conversation_id": "conv_local_1"}, headers=headers)
            steps.append({"step": f"tool {name}" + ("" if secret == "local-tool-secret" else " (no secret)"),
                          "arguments": args, "http": r.status_code, "answer": r.json().get("result", r.json())})

        tool("check_availability", {"date": "2026-11-18"}, secret=None)
        tool("check_availability", {"date": "2026-11-18"})
        tool("book_appointment", {"name": "Anna Silver", "phone": "07700 900123", "date": "2026-11-18",
                                  "time": "09:00", "reason": "Check-up (new patient)"})
        tool("book_appointment", {"name": "Ben Ortiz", "phone": "07700 900456", "date": "2026-11-18",
                                  "time": "09:00", "reason": "Check-up"})
        tool("capture_lead", {"name": "Ben Ortiz", "phone": "07700 900456", "interest": "Teeth whitening",
                              "consent_to_contact": False})
        tool("capture_lead", {"name": "Ben Ortiz", "phone": "07700 900456", "interest": "Teeth whitening",
                              "consent_to_contact": True})
        tool("handover", {"caller_name": "Ben Ortiz", "callback_number": "07700 900456",
                          "reason": "Broken tooth, in pain", "urgent": True})

        raw = (ROOT / "examples" / "sample_post_call.json").read_text(encoding="utf-8")
        payload = json.loads(raw)
        payload["data"]["conversation_id"] = "conv_local_1"
        payload["data"]["metadata"]["start_time_unix_secs"] = int(NOW.timestamp())
        payload["data"]["metadata"]["cost"] = 1200  # over the 1,000-credit daily budget on purpose
        raw = json.dumps(payload)
        for label, secret, at in [("webhook signed with the real secret", "local-webhook-secret", None),
                                  ("same webhook again (an ElevenLabs retry)", "local-webhook-secret", None),
                                  ("webhook signed with a wrong secret (forged)", "wrong-secret", None),
                                  ("webhook with a 2-hour-old signature (replay)", "local-webhook-secret",
                                   int(time.time()) - 7200)]:
            r = client.post("/webhooks/elevenlabs", content=raw,
                            headers={HEADER: sign(raw, secret, at), "Content-Type": "application/json"})
            steps.append({"step": label, "http": r.status_code, "answer": r.json()})

        guard = client.get("/guard", headers=admin).json()
        steps.append({"step": "cost guard after a 1,200-credit call (budget 1,000)", "http": 200, "answer": guard})
        tool("check_availability", {"date": "2026-11-19"})
        calls = client.get("/calls", headers=admin).json()
        leads = client.get("/leads", headers=admin).json()
        outbox = [json.loads(line) for line in (Path(tmp) / "outbox.jsonl").read_text().splitlines()]

    out = {"run": "offline, faked clock " + NOW.isoformat(), "steps": steps, "calls": calls, "leads": leads,
           "team_emails": outbox, "alerts": alerts}
    dest = ROOT / "examples" / "local-run" / "run.json"
    dest.write_text(json.dumps(out, indent=2, default=str) + "\n", encoding="utf-8")
    for s in steps:
        print(f"{s['http']}  {s['step']}: {str(s['answer'])[:110]}")
    print(f"wrote {dest.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
