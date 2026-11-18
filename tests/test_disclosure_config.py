import pytest

from voiceagent.config import Settings
from voiceagent.disclosure import (
    DEFAULT_HANDOVER,
    RECORDING_NOTICE,
    TRANSCRIPT_NOTICE,
    first_message,
    resolve_disclosure,
)


def test_default_first_message_order():
    s = Settings()
    msg = s.first_message
    assert msg.startswith("Hi, this is Cimo, an AI assistant for Harbour Dental Clinic.")
    assert RECORDING_NOTICE in msg and msg.endswith("How can I help today?")
    assert msg.index("AI assistant") < msg.index("recorded")


def test_transcript_only_notice():
    assert TRANSCRIPT_NOTICE in Settings(record_audio=False).first_message


def test_blank_disclosure_falls_back():
    assert resolve_disclosure("", "", "Cimo", "X") == "Hi, this is Cimo, an AI assistant for X."
    assert resolve_disclosure("   ", None, "Cimo", "X").startswith("Hi, this is Cimo")


def test_custom_disclosure_filled():
    assert resolve_disclosure("Hello from {business}'s AI, {name}.", "", "Ava", "Acme") == "Hello from Acme's AI, Ava."


def test_removed_only_with_written_request():
    s = Settings(ai_disclosure="", ai_disclosure_removed_by_written_request="2026-11-20 email from owner")
    assert s.disclosure == "" and s.handover_notice == ""
    assert s.first_message.startswith(RECORDING_NOTICE)  # the recording notice never goes


def test_handover_notice_default():
    assert Settings().handover_notice == DEFAULT_HANDOVER


def test_first_message_skips_empty_parts():
    assert first_message("", True, "") == RECORDING_NOTICE


@pytest.mark.parametrize("kwargs", [
    {"team_email": "a@x.com,b@x.com"}, {"team_email": "nope"}, {"team_email": "a@x.com;b@x.com"},
    {"max_call_seconds": 10}, {"max_call_seconds": 7200}, {"daily_call_limit": 0},
    {"daily_credit_budget": 0}, {"llm": "claude-sonnet-latest"}, {"llm": "gemini-3-flash-preview"},
])
def test_bad_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        Settings(**kwargs)


def test_from_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("RECORD_AUDIO", "0")
    monkeypatch.setenv("MAX_CALL_SECONDS", "120")
    monkeypatch.setenv("PAUSED", "yes")
    monkeypatch.setenv("AGENT_LLM", "claude-haiku-5-5")
    s = Settings.from_env()
    assert s.record_audio is False and s.max_call_seconds == 120 and s.paused is True
    assert s.llm == "claude-haiku-5-5" and s.data_dir == tmp_path


def test_defaults_are_pinned_stable_ids():
    s = Settings()
    assert s.llm == "claude-sonnet-5-5" and s.agent_tts_model == "eleven_flash_v2"


def test_english_agents_need_flash_or_turbo_v2():
    """Live finding 17 Nov 2026: ElevenLabs refuses English agents on v2.5 TTS models."""
    with pytest.raises(ValueError, match="English agents"):
        Settings(agent_tts_model="eleven_flash_v2_5")
    assert Settings(language="es", agent_tts_model="eleven_flash_v2_5").agent_tts_model == "eleven_flash_v2_5"


def test_env_picks_tts_model_by_language(monkeypatch):
    monkeypatch.setenv("AGENT_LANGUAGE", "fr")
    assert Settings.from_env().agent_tts_model == "eleven_flash_v2_5"
    monkeypatch.setenv("AGENT_LANGUAGE", "en")
    assert Settings.from_env().agent_tts_model == "eleven_flash_v2"
