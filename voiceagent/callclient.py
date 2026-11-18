"""Turn-taking logic for a scripted test call over the ElevenLabs agent WebSocket.

Pure and synchronous, so it is unit-tested without a network: it decides what audio to send next
(a caller clip or silence) from the events received so far. scripts/live_call.py does the I/O.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

SAMPLE_RATE = 16000
CHUNK_MS = 50
CHUNK_BYTES = SAMPLE_RATE * 2 * CHUNK_MS // 1000  # 16-bit mono
SILENCE = b"\x00" * CHUNK_BYTES


@dataclass
class Turn:
    role: str
    text: str
    at: float


@dataclass
class CallScript:
    clips: List[bytes]                     # raw PCM16 16 kHz mono, one per caller turn
    agent_quiet_secs: float = 1.0          # pause after the agent's audio has finished PLAYING
    max_secs: float = 75.0                 # hard stop for the whole call (credit guard)
    started: float = 0.0
    next_clip: int = 0
    playing: Optional[bytearray] = None
    last_agent_audio: float = -1.0
    agent_audio_ends: float = -1.0         # when the agent's audio received so far finishes playing
    agent_spoke_since_clip: bool = False
    conversation_id: Optional[str] = None
    transcript: List[Turn] = field(default_factory=list)
    tool_events: List[Dict[str, Any]] = field(default_factory=list)
    agent_pcm: bytearray = field(default_factory=bytearray)
    done: bool = False
    end_reason: str = ""

    def handle(self, raw: str, now: float) -> Optional[str]:
        """Process one server message. Returns a message to send back (a pong) or None."""
        msg = json.loads(raw)
        kind = msg.get("type")
        if kind == "conversation_initiation_metadata":
            meta = msg.get("conversation_initiation_metadata_event") or {}
            self.conversation_id = meta.get("conversation_id")
        elif kind == "audio":
            ev = msg.get("audio_event") or {}
            b64 = ev.get("audio_base_64")
            if b64:
                pcm = base64.b64decode(b64)
                self.agent_pcm.extend(pcm)
                # ElevenLabs streams audio FASTER than real time (live finding, 17 Nov 2026), so the agent is
                # "speaking" until the audio received so far would have finished playing, not until it arrives.
                self.agent_audio_ends = max(self.agent_audio_ends, now) + len(pcm) / (SAMPLE_RATE * 2)
            self.last_agent_audio = now
            self.agent_spoke_since_clip = True
        elif kind == "agent_response":
            text = (msg.get("agent_response_event") or {}).get("agent_response", "")
            self.transcript.append(Turn("agent", text, now - self.started))
        elif kind == "user_transcript":
            text = (msg.get("user_transcription_event") or {}).get("user_transcript", "")
            self.transcript.append(Turn("user", text, now - self.started))
        elif kind in ("agent_tool_request", "agent_tool_response", "client_tool_call"):
            body = {k: v for k, v in msg.items() if k != "type"}
            self.tool_events.append({"type": kind, "at": round(now - self.started, 2), **body})
        elif kind == "ping":
            ev = msg.get("ping_event") or {}
            return json.dumps({"type": "pong", "event_id": ev.get("event_id")})
        return None

    def agent_speaking(self, now: float) -> bool:
        if self.last_agent_audio < 0:
            return False
        return now < max(self.agent_audio_ends, self.last_agent_audio) + self.agent_quiet_secs

    def next_chunk(self, now: float) -> bytes:
        """The next 50 ms of caller audio: the current clip, or silence while the agent talks/thinks."""
        if now - self.started > self.max_secs:
            self.done, self.end_reason = True, f"hit the {self.max_secs:.0f} s test cap"
            return SILENCE
        if self.playing:
            chunk = bytes(self.playing[:CHUNK_BYTES])
            del self.playing[:CHUNK_BYTES]
            if not self.playing:
                self.playing = None
                self.agent_spoke_since_clip = False
            return chunk.ljust(CHUNK_BYTES, b"\x00")
        if self.agent_spoke_since_clip and not self.agent_speaking(now):
            if self.next_clip < len(self.clips):
                self.playing = bytearray(self.clips[self.next_clip])
                self.next_clip += 1
                return self.next_chunk(now)
            self.done, self.end_reason = True, "script finished"
        return SILENCE

    @staticmethod
    def chunk_message(chunk: bytes) -> str:
        return json.dumps({"user_audio_chunk": base64.b64encode(chunk).decode()})
