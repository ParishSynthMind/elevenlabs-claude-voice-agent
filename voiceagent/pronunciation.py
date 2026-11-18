"""Pronunciation dictionary: rules in data/pronunciation.json, validated here, uploaded to ElevenLabs
(add-from-rules) and used by BOTH the voice agent and the narration script.

Alias rules ("say X as Y") are what ElevenLabs recommends for models without phoneme support. Phoneme rules (IPA or CMU) only take effect
on models that support phoneme tags, so phoneme_warnings() flags each one for a listening check.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Tuple
from xml.sax.saxutils import escape

PHONEME_ALPHABETS = {"ipa", "cmu-arpabet"}
MAX_RULES = 1000


class DictionaryError(ValueError):
    pass


def load(path: Path) -> Dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    validate(data["rules"])
    return data


def validate(rules: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(rules, list) or not rules:
        raise DictionaryError("a dictionary needs at least one rule")
    if len(rules) > MAX_RULES:
        raise DictionaryError(f"too many rules ({len(rules)} > {MAX_RULES})")
    seen = set()
    for i, r in enumerate(rules):
        word = (r.get("string_to_replace") or "").strip()
        if not word:
            raise DictionaryError(f"rule {i}: string_to_replace must be a non-empty string")
        key = (word.lower() if r.get("case_sensitive") is False else word)
        if key in seen:
            raise DictionaryError(f"rule {i}: '{word}' appears twice")
        seen.add(key)
        kind = r.get("type")
        if kind == "alias":
            if not (r.get("alias") or "").strip():
                raise DictionaryError(f"rule {i}: alias rule for '{word}' has no alias")
        elif kind == "phoneme":
            if not (r.get("phoneme") or "").strip():
                raise DictionaryError(f"rule {i}: phoneme rule for '{word}' has no phoneme")
            if r.get("alphabet") not in PHONEME_ALPHABETS:
                raise DictionaryError(f"rule {i}: alphabet must be one of {sorted(PHONEME_ALPHABETS)}")
        else:
            raise DictionaryError(f"rule {i}: type must be 'alias' or 'phoneme'")
        extra = set(r) - {"string_to_replace", "type", "alias", "phoneme", "alphabet", "case_sensitive", "word_boundaries"}
        if extra:
            raise DictionaryError(f"rule {i}: unknown field(s) {sorted(extra)}")
    return rules


def apply_aliases(text: str, rules: List[Dict[str, Any]]) -> str:
    """Local preview of what the alias rules do (ElevenLabs applies them server-side)."""
    import re
    out = text
    for r in rules:
        if r.get("type") != "alias":
            continue
        flags = 0 if r.get("case_sensitive", True) else re.IGNORECASE
        word = re.escape(r["string_to_replace"])
        pattern = rf"\b{word}\b" if r.get("word_boundaries", True) else word
        out = re.sub(pattern, lambda _m, a=r["alias"]: a, out, flags=flags)
    return out


def to_pls(rules: List[Dict[str, Any]], lang: str = "en-GB") -> str:
    """The same rules as a W3C PLS lexicon file (the format ElevenLabs' add-from-file accepts)."""
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon" '
             f'alphabet="ipa" xml:lang="{escape(lang)}">']
    for r in rules:
        lines.append("  <lexeme>")
        lines.append(f"    <grapheme>{escape(r['string_to_replace'])}</grapheme>")
        if r["type"] == "alias":
            lines.append(f"    <alias>{escape(r['alias'])}</alias>")
        else:
            lines.append(f"    <phoneme>{escape(r['phoneme'])}</phoneme>")
        lines.append("  </lexeme>")
    lines.append("</lexicon>")
    return "\n".join(lines) + "\n"


def phoneme_warnings(rules: List[Dict[str, Any]], model_id: str) -> List[Tuple[str, str]]:
    """Phoneme rules only take effect on models with phoneme-tag support, and ElevenLabs does not list
    that support per model in its API. Rather than guess, flag every phoneme rule so it gets a listening
    check on the chosen model; alias rules need no check."""
    return [(r["string_to_replace"], f"phoneme rule: listen to a sample on {model_id} before relying on it")
            for r in rules if r.get("type") == "phoneme"]
