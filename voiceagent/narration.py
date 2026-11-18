"""Voice-over / narration with a chosen voice and the pronunciation dictionary.

Credit-safe by design: the text is measured BEFORE anything is sent, scripts over the character cap
are refused unless the cap is raised on purpose, `--dry-run` shows the estimate and sends nothing,
and every real run writes a sidecar JSON next to the audio that labels it AI-generated (voice,
model, dictionary version, characters, credits used as measured from the account before and after).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Estimated credits per character. ElevenLabs' API pricing page (checked 17 Nov 2026) prices Flash/Turbo
# at half of Multilingual ($0.04 vs $0.08 per 1,000 characters), so Flash/Turbo count as 0.5 and the
# rest as 1. Only an ESTIMATE shown before a run; the real figure is measured from the account after it.
CREDITS_PER_CHAR = {"eleven_flash_v2_5": 0.5, "eleven_flash_v2": 0.5, "eleven_turbo_v2_5": 0.5,
                    "eleven_turbo_v2": 0.5}


class NarrationError(ValueError):
    pass


@dataclass
class NarrationPlan:
    text: str
    characters: int
    model_id: str
    voice_id: str
    estimated_credits: int
    dictionary: Optional[Dict[str, str]]


def clean_script(text: str) -> str:
    lines = [ln.strip() for ln in text.replace("\r\n", "\n").split("\n")]
    out = " ".join(ln for ln in lines if ln and not ln.startswith("#"))
    return " ".join(out.split())


def estimate_credits(characters: int, model_id: str) -> int:
    rate = CREDITS_PER_CHAR.get(model_id, 1.0)
    return int(characters * rate + 0.999)


def plan(text: str, model_id: str, voice_id: str, max_chars: int,
         dictionary: Optional[Dict[str, str]] = None) -> NarrationPlan:
    script = clean_script(text)
    if not script:
        raise NarrationError("the script is empty")
    if len(script) > max_chars:
        raise NarrationError(f"the script is {len(script)} characters, over the {max_chars}-character cap. "
                             f"That is about {estimate_credits(len(script), model_id)} credits. Raise "
                             f"NARRATION_MAX_CHARS on purpose if this is really wanted.")
    return NarrationPlan(script, len(script), model_id, voice_id, estimate_credits(len(script), model_id), dictionary)


def sidecar(p: NarrationPlan, audio_file: Path, request_id: Optional[str], credits_used: Optional[int],
            extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "ai_generated": True,
        "label": "AI-generated voice (ElevenLabs text to speech)",
        "generator": "ElevenLabs",
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "audio_file": audio_file.name,
        "request_id": request_id,
        "credits_used_measured": credits_used,
    }
    meta.update({k: v for k, v in asdict(p).items() if k != "text"})
    meta["text"] = p.text
    if extra:
        meta.update(extra)
    return meta


def narrate(client, p: NarrationPlan, out_dir: Path, stem: str) -> Dict[str, Any]:
    """Send ONE request, save the mp3 and its sidecar JSON. Measures credits from the account."""
    out_dir.mkdir(parents=True, exist_ok=True)
    before = client.subscription().get("character_count")
    dictionaries: Optional[List[Dict[str, str]]] = [p.dictionary] if p.dictionary else None
    resp = client.text_to_speech(p.voice_id, p.text, p.model_id, dictionaries)
    audio = out_dir / f"{stem}.mp3"
    audio.write_bytes(resp.content)
    after = client.subscription().get("character_count")
    used = (after - before) if isinstance(before, int) and isinstance(after, int) else None
    meta = sidecar(p, audio, resp.headers.get("request-id"), used,
                   {"bytes": len(resp.content), "content_type": resp.headers.get("content-type")})
    (out_dir / f"{stem}.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
