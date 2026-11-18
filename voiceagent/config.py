from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .disclosure import first_message, handover_notice, resolve_disclosure

# Weekday (0 = Monday) -> list of (open, close) in "HH:MM", business local time.
DEFAULT_HOURS: Dict[int, List[Tuple[str, str]]] = {
    0: [("09:00", "17:00")],
    1: [("09:00", "17:00")],
    2: [("09:00", "17:00")],
    3: [("09:00", "17:00")],
    4: [("09:00", "17:00")],
    5: [("10:00", "14:00")],
    6: [],
}

# Pinned platform versions (Vibe Coding Playbook rule B17). Each is ONE config value; change on purpose.
ELEVENLABS_API_BASE = "https://api.elevenlabs.io"
DEFAULT_LLM = "claude-sonnet-5-5"           # ElevenLabs Agents' native Claude Sonnet 5.5 (llm enum)
# ElevenLabs refuses English agents on v2.5 TTS ("English Agents must use turbo or flash v2", found in the
# live run 17 Nov 2026), so English agents use Flash v2 and other languages Flash v2.5.
ENGLISH_AGENT_TTS_MODELS = ("eleven_flash_v2", "eleven_turbo_v2")
DEFAULT_AGENT_TTS_MODEL = "eleven_flash_v2"
DEFAULT_AGENT_TTS_MODEL_MULTILINGUAL = "eleven_flash_v2_5"
DEFAULT_NARRATION_MODEL = "eleven_flash_v2_5"
DEFAULT_VOICE_ID = "JBFqnCBsd6RMkjVDRZzb"   # "George", an ElevenLabs default (premade) voice


def _bool(value: Optional[str], default: bool) -> bool:
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    business_name: str = "Harbour Dental Clinic"
    agent_name: str = "Cimo"
    timezone: str = "Europe/London"
    slot_minutes: int = 30
    min_notice_minutes: int = 60
    hours: Dict[int, List[Tuple[str, str]]] = field(default_factory=lambda: dict(DEFAULT_HOURS))
    data_dir: Path = Path("var")
    knowledge_base: Path = Path(__file__).resolve().parent.parent / "data" / "knowledge_base.md"
    pronunciation_file: Path = Path(__file__).resolve().parent.parent / "data" / "pronunciation.json"

    # Security
    tool_secret: Optional[str] = None      # shared secret ElevenLabs sends in X-Agent-Tool-Secret on tool calls
    webhook_secret: Optional[str] = None   # HMAC secret of the post-call webhook (ElevenLabs-Signature)
    allow_unsigned: bool = False           # local development only; never on a public server
    admin_token: Optional[str] = None      # bearer token for /calls, /leads, /calendar.ics, /guard

    # Agent (ElevenLabs side)
    llm: str = DEFAULT_LLM
    agent_tts_model: str = DEFAULT_AGENT_TTS_MODEL
    voice_id: str = DEFAULT_VOICE_ID
    language: str = "en"
    record_audio: bool = True              # ElevenLabs privacy.record_voice; also picks the recording notice
    retention_days: int = 30               # how long ElevenLabs keeps transcripts and audio

    # Cost guard (per call, per day)
    max_call_seconds: int = 300            # ElevenLabs ends every call at this length
    daily_call_limit: int = 50             # ElevenLabs refuses new calls past this many a day
    daily_credit_budget: int = 20000       # our own cap on credits spent per day (sum of webhook costs)
    paused: bool = False                   # kill switch: tools stop acting, calls get a "take a message"

    # Narration (text to speech)
    narration_model: str = DEFAULT_NARRATION_MODEL
    narration_max_chars: int = 600         # one request; longer scripts need an explicit raise

    # Calendar and email
    calendar_backend: str = "local"
    google_calendar_id: Optional[str] = None
    google_service_account_file: Optional[str] = None
    google_service_account_json: Optional[str] = None
    team_email: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587
    smtp_user: Optional[str] = None
    smtp_password: Optional[str] = None
    smtp_from: Optional[str] = None

    # AI disclosure (EU AI Act Art. 50). See voiceagent/disclosure.py.
    ai_disclosure: str = ""
    ai_disclosure_removed_by_written_request: str = ""

    @property
    def disclosure(self) -> str:
        return resolve_disclosure(self.ai_disclosure, self.ai_disclosure_removed_by_written_request,
                                  self.agent_name, self.business_name)

    @property
    def handover_notice(self) -> str:
        return handover_notice(self.ai_disclosure_removed_by_written_request)

    @property
    def first_message(self) -> str:
        return first_message(self.disclosure, self.record_audio)

    def __post_init__(self) -> None:
        # Summary, lead and handover emails go to ONE fixed address from the config. Nothing a caller
        # says, and nothing the model puts in a tool call, can change the recipient.
        if self.team_email and ("," in self.team_email or ";" in self.team_email or "@" not in self.team_email):
            raise ValueError("TEAM_EMAIL must be a single email address")
        if not 30 <= self.max_call_seconds <= 3600:
            raise ValueError("MAX_CALL_SECONDS must be between 30 and 3600")
        if self.daily_call_limit < 1:
            raise ValueError("DAILY_CALL_LIMIT must be at least 1 (use PAUSED=1 to stop calls)")
        if self.daily_credit_budget < 1:
            raise ValueError("DAILY_CREDIT_BUDGET must be at least 1")
        if self.language == "en" and self.agent_tts_model not in ENGLISH_AGENT_TTS_MODELS:
            raise ValueError(f"English agents must use one of {ENGLISH_AGENT_TTS_MODELS} (ElevenLabs rule)")
        if self.llm.endswith("-latest") or "preview" in self.llm:
            raise ValueError("Use a stable model id, never a preview or -latest alias (playbook rule B10)")

    @classmethod
    def from_env(cls) -> "Settings":
        env = os.environ.get
        return cls(
            business_name=env("BUSINESS_NAME", cls.business_name),
            agent_name=env("AGENT_NAME", cls.agent_name),
            timezone=env("BUSINESS_TZ", cls.timezone),
            slot_minutes=int(env("SLOT_MINUTES", str(cls.slot_minutes))),
            min_notice_minutes=int(env("MIN_NOTICE_MINUTES", str(cls.min_notice_minutes))),
            data_dir=Path(env("DATA_DIR", "var")),
            tool_secret=env("AGENT_TOOL_SECRET"),
            webhook_secret=env("ELEVENLABS_WEBHOOK_SECRET"),
            allow_unsigned=env("ALLOW_UNSIGNED", "") == "1",
            admin_token=env("ADMIN_TOKEN"),
            llm=env("AGENT_LLM", cls.llm),
            agent_tts_model=env("AGENT_TTS_MODEL") or (DEFAULT_AGENT_TTS_MODEL if env("AGENT_LANGUAGE", "en") == "en"
                                                       else DEFAULT_AGENT_TTS_MODEL_MULTILINGUAL),
            voice_id=env("VOICE_ID", cls.voice_id),
            language=env("AGENT_LANGUAGE", cls.language),
            record_audio=_bool(env("RECORD_AUDIO"), True),
            retention_days=int(env("RETENTION_DAYS", str(cls.retention_days))),
            max_call_seconds=int(env("MAX_CALL_SECONDS", str(cls.max_call_seconds))),
            daily_call_limit=int(env("DAILY_CALL_LIMIT", str(cls.daily_call_limit))),
            daily_credit_budget=int(env("DAILY_CREDIT_BUDGET", str(cls.daily_credit_budget))),
            paused=_bool(env("PAUSED"), False),
            narration_model=env("NARRATION_MODEL", cls.narration_model),
            narration_max_chars=int(env("NARRATION_MAX_CHARS", str(cls.narration_max_chars))),
            calendar_backend=env("CALENDAR_BACKEND", "local"),
            google_calendar_id=env("GOOGLE_CALENDAR_ID"),
            google_service_account_file=env("GOOGLE_SERVICE_ACCOUNT_FILE"),
            google_service_account_json=env("GOOGLE_SERVICE_ACCOUNT_JSON"),
            team_email=env("TEAM_EMAIL"),
            smtp_host=env("SMTP_HOST"),
            smtp_port=int(env("SMTP_PORT", "587")),
            smtp_user=env("SMTP_USER"),
            smtp_password=env("SMTP_PASSWORD"),
            smtp_from=env("SMTP_FROM") or env("SMTP_USER"),
            ai_disclosure=env("AI_DISCLOSURE", ""),
            ai_disclosure_removed_by_written_request=env("AI_DISCLOSURE_REMOVED_BY_WRITTEN_REQUEST", ""),
        )
