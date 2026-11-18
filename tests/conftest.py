from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from voiceagent.calendar.local import LocalCalendar
from voiceagent.calllog import CallLog
from voiceagent.config import DEFAULT_HOURS, Settings
from voiceagent.costguard import CostGuard
from voiceagent.scheduling import Scheduler
from voiceagent.tools import VoiceAgentTools

TZ = "Europe/London"
# Monday 3 November 2025, 08:00 local: the whole working day is still ahead.
FIXED_NOW = datetime(2025, 11, 3, 8, 0, tzinfo=ZoneInfo(TZ))


class FakeNotifier:
    def __init__(self):
        self.sent = []

    def send(self, subject, body):
        self.sent.append((subject, body))


class Alerts:
    def __init__(self):
        self.sent = []

    def __call__(self, subject, detail):
        self.sent.append((subject, detail))


@pytest.fixture
def notifier():
    return FakeNotifier()


@pytest.fixture
def alerts():
    return Alerts()


@pytest.fixture
def calendar(tmp_path: Path):
    return LocalCalendar(tmp_path / "cal.sqlite3")


@pytest.fixture
def calllog(tmp_path):
    return CallLog(tmp_path / "calls.sqlite3")


@pytest.fixture
def guard(calllog, alerts):
    return CostGuard(calllog, TZ, daily_credit_budget=1000, daily_call_limit=5, alert=alerts,
                     now=lambda: FIXED_NOW)


@pytest.fixture
def tools(calendar, notifier, calllog, guard, alerts):
    sched = Scheduler(calendar, TZ, DEFAULT_HOURS, 30, 60)
    sched.now = lambda: FIXED_NOW
    return VoiceAgentTools(sched, calendar, notifier, calllog, "Test Clinic", guard, on_error=alerts)


@pytest.fixture
def settings(tmp_path):
    return Settings(data_dir=tmp_path, tool_secret="tool-secret-123", webhook_secret="whsec-abc",
                    admin_token="admin-xyz", timezone=TZ)
