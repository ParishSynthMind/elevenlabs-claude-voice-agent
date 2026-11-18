"""Cost guard: a per-call cap, a daily call cap and a daily credit budget, plus a kill switch.

Three layers, so one missing layer never means an open tap:
1. ElevenLabs itself ends every call at `max_call_seconds` and refuses calls past `daily_call_limit`
   (set on the agent by scripts/setup_agent.py: conversation.max_duration_seconds and
   platform_settings.call_limits.daily_limit).
2. This server adds up the credits ElevenLabs reports in each post-call webhook (metadata.cost).
   Once today's total reaches `daily_credit_budget`, the guard trips: tools stop booking and
   capturing (the agent offers to take a message) and an alert goes out once.
3. `paused` (PAUSED=1, or POST /guard/pause) is the kill switch for everything the agent can do.
   scripts/setup_agent.py --pause also sets the agent's daily call limit to its lowest value so
   ElevenLabs stops taking new calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional
from zoneinfo import ZoneInfo

from .calllog import CallLog


@dataclass
class GuardState:
    allowed: bool
    reason: str
    credits_today: int
    calls_today: int


class CostGuard:
    def __init__(self, log: CallLog, timezone: str, daily_credit_budget: int, daily_call_limit: int,
                 paused: bool = False, alert: Optional[Callable[[str, str], None]] = None,
                 now: Optional[Callable[[], datetime]] = None):
        self.log = log
        self.tz = ZoneInfo(timezone)
        self.daily_credit_budget = daily_credit_budget
        self.daily_call_limit = daily_call_limit
        self.paused = paused
        self.alert = alert
        self._now = now or (lambda: datetime.now(self.tz))
        self._alerted_day: Optional[str] = None

    def today(self) -> str:
        return self._now().astimezone(self.tz).date().isoformat()

    def state(self) -> GuardState:
        day = self.today()
        credits, calls = self.log.credits_on(day), self.log.calls_on(day)
        if self.paused:
            return GuardState(False, "paused (kill switch)", credits, calls)
        if credits >= self.daily_credit_budget:
            return GuardState(False, f"daily credit budget reached ({credits} of {self.daily_credit_budget})",
                              credits, calls)
        if calls > self.daily_call_limit:
            return GuardState(False, f"daily call limit passed ({calls} of {self.daily_call_limit})", credits, calls)
        return GuardState(True, "ok", credits, calls)

    def check(self) -> GuardState:
        """State, plus ONE alert per day the first time the guard trips for a budget or limit."""
        st = self.state()
        if not st.allowed and not self.paused and self._alerted_day != self.today():
            self._alerted_day = self.today()
            if self.alert:
                self.alert("Voice agent cost guard tripped",
                           f"The voice agent stopped acting on calls: {st.reason}. Calls today: {st.calls_today}. "
                           f"Raise DAILY_CREDIT_BUDGET / DAILY_CALL_LIMIT or wait for tomorrow.")
        return st

    def pause(self) -> None:
        self.paused = True

    def resume(self) -> None:
        self.paused = False
