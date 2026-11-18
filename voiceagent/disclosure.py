"""AI disclosure (EU AI Act Article 50) and the recording notice, spoken in the agent's first message.

The AI line comes from AI_DISCLOSURE. Blanking it does NOT switch it off: that only happens when
AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST holds a reference to the client's written request
(date and who asked). Without that reference an empty value falls back to the default.

The recording notice follows the agent's privacy setting: if audio is recorded the caller is told so;
if only the transcript is kept, the caller is told that instead. It can't be blanked: a call that is
recorded or transcribed always says so.
"""
from __future__ import annotations

from typing import Optional

DEFAULT_DISCLOSURE = "Hi, this is {name}, an AI assistant for {business}."
DEFAULT_HANDOVER = "I'm an AI assistant, so I'm handing you over to a person on the team."
RECORDING_NOTICE = "This call is recorded and transcribed so the team can follow up."
TRANSCRIPT_NOTICE = "This call is transcribed so the team can follow up."
DEFAULT_QUESTION = "How can I help today?"


def _fill(text: str, name: str, business: str) -> str:
    return text.replace("{name}", name).replace("{business}", business).strip()


def resolve_disclosure(text: Optional[str], removed_by_written_request: Optional[str],
                       name: str, business: str) -> str:
    """The opening disclosure line. Empty only when a written-request reference is recorded."""
    if (removed_by_written_request or "").strip():
        return _fill(text or "", name, business)
    return _fill((text or "").strip() or DEFAULT_DISCLOSURE, name, business)


def handover_notice(removed_by_written_request: Optional[str]) -> str:
    """What the caller hears when they are passed to a person."""
    return "" if (removed_by_written_request or "").strip() else DEFAULT_HANDOVER


def recording_notice(record_audio: bool) -> str:
    return RECORDING_NOTICE if record_audio else TRANSCRIPT_NOTICE


def first_message(disclosure: str, record_audio: bool, question: str = DEFAULT_QUESTION) -> str:
    """Disclosure, then the recording notice, then the question: the AI line always comes first."""
    parts = [disclosure.strip(), recording_notice(record_audio), question.strip()]
    return " ".join(p for p in parts if p)
