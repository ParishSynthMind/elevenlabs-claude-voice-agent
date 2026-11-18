#!/usr/bin/env python3
"""Narration / voice-over with a chosen voice and the pronunciation dictionary.

  python scripts/narrate.py script.txt --dry-run                  # characters and estimated credits, sends nothing
  python scripts/narrate.py script.txt --voice JBFqnCBsd6RMkjVDRZzb --out out/ --dictionary-from-state
  python scripts/narrate.py script.txt --dictionary ID:VERSION

Refuses scripts over NARRATION_MAX_CHARS (default 600) so a whole script is never sent by accident.
Writes <stem>.mp3 and <stem>.json (AI-generated label, voice, model, dictionary, characters, credits).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voiceagent import narration, secrets_store  # noqa: E402
from voiceagent.config import Settings  # noqa: E402
from voiceagent.elevenlabs_api import ElevenLabsClient  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("script", type=Path)
    ap.add_argument("--voice")
    ap.add_argument("--model")
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--stem")
    ap.add_argument("--dictionary", help="pronunciation dictionary as ID:VERSION")
    ap.add_argument("--dictionary-from-state", action="store_true", help="use the one setup_agent.py created")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--keychain", default="elevenlabs-api-key")
    args = ap.parse_args(argv)
    s = Settings.from_env()
    dictionary = None
    if args.dictionary:
        did, _, ver = args.dictionary.partition(":")
        dictionary = {"pronunciation_dictionary_id": did, **({"version_id": ver} if ver else {})}
    elif args.dictionary_from_state:
        dictionary = json.loads((s.data_dir / "elevenlabs-state.json").read_text())["dictionary"]
    try:
        p = narration.plan(args.script.read_text(encoding="utf-8"), args.model or s.narration_model,
                           args.voice or s.voice_id, s.narration_max_chars, dictionary)
    except narration.NarrationError as e:
        print(f"Refused: {e}")
        return 2
    print(f"{p.characters} characters, about {p.estimated_credits} credits on {p.model_id}, voice {p.voice_id}"
          + (f", dictionary {dictionary['pronunciation_dictionary_id']}" if dictionary else ""))
    if args.dry_run:
        return 0
    key = secrets_store.get("ELEVENLABS_API_KEY", args.keychain)
    if not key:
        sys.exit("No ElevenLabs key: set ELEVENLABS_API_KEY or store it in the Keychain.")
    meta = narration.narrate(ElevenLabsClient(key), p, args.out, args.stem or args.script.stem)
    print(json.dumps({k: meta[k] for k in ("audio_file", "characters", "estimated_credits",
                                           "credits_used_measured", "request_id")}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
