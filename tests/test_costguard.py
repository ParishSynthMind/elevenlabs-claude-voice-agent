from voiceagent.tools import GUARD_MESSAGE

from .conftest import FIXED_NOW

DAY = FIXED_NOW.date().isoformat()


def test_allowed_by_default(guard):
    st = guard.check()
    assert st.allowed and st.reason == "ok"


def test_budget_trips_and_alerts_once(guard, calllog, alerts):
    calllog.mark("c1", day=DAY, credits=600)
    calllog.mark("c2", day=DAY, credits=450)
    assert not guard.check().allowed
    guard.check()
    assert len(alerts.sent) == 1 and "cost guard" in alerts.sent[0][0]


def test_other_days_do_not_count(guard, calllog):
    calllog.mark("old", day="2025-11-02", credits=99999)
    assert guard.check().allowed


def test_call_limit(guard, calllog):
    for i in range(6):
        calllog.mark(f"c{i}", day=DAY, credits=1)
    st = guard.check()
    assert not st.allowed and "call limit" in st.reason


def test_pause_and_resume(guard, alerts):
    guard.pause()
    st = guard.check()
    assert not st.allowed and "kill switch" in st.reason
    assert alerts.sent == []  # pausing on purpose is not an alert
    guard.resume()
    assert guard.check().allowed


def test_tripped_guard_blocks_actions_but_not_handover(tools, guard, notifier):
    guard.pause()
    args = {"name": "A", "phone": "1", "date": "2025-11-04", "time": "10:00"}
    assert tools.run("book_appointment", args, {"id": "c"}) == GUARD_MESSAGE
    assert tools.run("check_availability", {}, {"id": "c"}) == GUARD_MESSAGE
    assert tools.run("capture_lead", {"name": "A", "phone": "1", "consent_to_contact": True}, {"id": "c"}) == GUARD_MESSAGE
    out = tools.run("handover", {"reason": "wants a person", "urgent": False}, {"id": "c"})
    assert "will call" in out and notifier.sent


def test_budget_counts_null_credits_as_zero(guard, calllog):
    calllog.mark("c1", day=DAY)
    assert guard.state().credits_today == 0
