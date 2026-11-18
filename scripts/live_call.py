#!/usr/bin/env python3
"""A real ElevenLabs voice conversation with a scripted synthetic caller (no microphone needed).

  mkdir -p clips/booking
  say -o /tmp/1.aiff "Hi, do you have parking for patients?"
  afconvert -f WAVE -d LEI16@16000 -c 1 /tmp/1.aiff clips/booking/01.wav
  python scripts/live_call.py --clips clips/booking --out runs/booking

Gets a signed URL for the (auth-protected) agent, opens the agent WebSocket, streams each clip as
16 kHz PCM in real time when the agent has finished speaking, answers pings, and saves the
transcript, tool events and the agent's own audio (agent.wav). The call ends after the last clip
or at --max-secs (default 75), whichever comes first; the agent's own max duration also applies.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import websockets  # noqa: E402

from voiceagent import secrets_store  # noqa: E402
from voiceagent.callclient import CHUNK_MS, SAMPLE_RATE, CallScript  # noqa: E402
from voiceagent.config import Settings  # noqa: E402
from voiceagent.elevenlabs_api import ElevenLabsClient  # noqa: E402


def read_clip(path: Path) -> bytes:
    with wave.open(str(path)) as w:
        if w.getframerate() != SAMPLE_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            sys.exit(f"{path}: needs 16 kHz mono 16-bit (afconvert -f WAVE -d LEI16@16000 -c 1)")
        return w.readframes(w.getnframes())


async def run(url: str, script: CallScript) -> None:
    async with websockets.connect(url, max_size=16 * 1024 * 1024) as ws:
        await ws.send(json.dumps({"type": "conversation_initiation_client_data"}))
        script.started = time.monotonic()

        async def receive():
            async for raw in ws:
                reply = script.handle(raw, time.monotonic())
                if reply:
                    await ws.send(reply)

        receiver = asyncio.create_task(receive())
        try:
            while not script.done and not receiver.done():
                await ws.send(script.chunk_message(script.next_chunk(time.monotonic())))
                await asyncio.sleep(CHUNK_MS / 1000)
            # let the agent finish its last sentence
            end = time.monotonic() + 3
            while time.monotonic() < end and not receiver.done():
                await ws.send(script.chunk_message(b"\x00" * 1600))
                await asyncio.sleep(CHUNK_MS / 1000)
        finally:
            receiver.cancel()
            await ws.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-secs", type=float, default=75)
    ap.add_argument("--keychain", default="elevenlabs-api-key")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    state = json.loads((settings.data_dir / "elevenlabs-state.json").read_text())
    key = secrets_store.get("ELEVENLABS_API_KEY", args.keychain)
    client = ElevenLabsClient(key)
    clips = [read_clip(p) for p in sorted(args.clips.glob("*.wav"))]
    script = CallScript(clips=clips, max_secs=args.max_secs)
    url = client.signed_url(state["agent_id"])
    asyncio.run(run(url, script))
    args.out.mkdir(parents=True, exist_ok=True)
    with wave.open(str(args.out / "agent.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(bytes(script.agent_pcm))
    result = {"conversation_id": script.conversation_id, "end_reason": script.end_reason,
              "transcript": [t.__dict__ for t in script.transcript], "tool_events": script.tool_events}
    (args.out / "call.json").write_text(json.dumps(result, indent=2))
    for t in script.transcript:
        print(f"{t.at:6.1f}s {t.role:5}: {t.text}")
    print(f"conversation {script.conversation_id}; {script.end_reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
