import json

import pytest
from fastapi.testclient import TestClient

from voiceagent.app import create_app
from voiceagent.config import Settings
from voiceagent.signature import HEADER, TOOL_HEADER, sign

from .conftest import FIXED_NOW


@pytest.fixture
def client(settings, tools):
    return TestClient(create_app(settings, tools))


def tool_post(client, name, args, secret="tool-secret-123"):
    headers = {TOOL_HEADER: secret} if secret is not None else {}
    return client.post(f"/tools/{name}", json=args, headers=headers)


def payload(conv="conv_9", cost=200, start=None, kind="post_call_transcription"):
    return json.dumps({"type": kind, "event_timestamp": 1, "data": {
        "agent_id": "agent_x", "conversation_id": conv, "status": "done",
        "transcript": [{"role": "agent", "message": "Hi, this is Cimo, an AI assistant.", "tool_calls": None},
                       {"role": "user", "message": "Parking?", "tool_calls": None},
                       {"role": "agent", "message": "", "tool_calls": [{"tool_name": "check_availability"}]}],
        "metadata": {"start_time_unix_secs": start or int(FIXED_NOW.timestamp()), "call_duration_secs": 30,
                     "cost": cost, "termination_reason": "end_call tool was called."},
        "analysis": {"call_successful": "success", "transcript_summary": "Asked about parking."}}})


def hook(client, raw, secret="whsec-abc"):
    return client.post("/webhooks/elevenlabs", content=raw,
                       headers={HEADER: sign(raw, secret), "Content-Type": "application/json"})


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_tool_with_secret(client, tools):
    r = tool_post(client, "check_availability", {"conversation_id": "conv_1"})
    assert r.status_code == 200 and r.json()["result"].startswith("Today is")
    assert tools.log.calls()[0]["call_id"] == "conv_1"


@pytest.mark.parametrize("secret", [None, "", "wrong", "tool-secret-1234"])
def test_tool_without_right_secret_401(client, secret):
    assert tool_post(client, "check_availability", {}, secret).status_code == 401


def test_unknown_tool_404(client):
    assert tool_post(client, "rm_rf", {}).status_code == 404


def test_tool_bad_json(client):
    r = client.post("/tools/handover", content="not json", headers={TOOL_HEADER: "tool-secret-123"})
    assert r.status_code == 400


def test_tool_array_body_400(client):
    r = client.post("/tools/handover", json=[1, 2], headers={TOOL_HEADER: "tool-secret-123"})
    assert r.status_code == 400


def test_tool_refused_without_configured_secret(tmp_path, tools):
    c = TestClient(create_app(Settings(data_dir=tmp_path), tools))
    assert c.post("/tools/handover", json={}).status_code == 503


def test_unsigned_allowed_only_in_dev_mode(tmp_path, tools):
    c = TestClient(create_app(Settings(data_dir=tmp_path, allow_unsigned=True), tools))
    assert c.post("/tools/handover", json={"reason": "x", "urgent": False}).status_code == 200


def test_body_too_large(client):
    r = client.post("/tools/handover", content="x" * (2 * 1024 * 1024 + 10), headers={TOOL_HEADER: "tool-secret-123"})
    assert r.status_code == 413


def test_webhook_logs_and_emails_once(client, tools, notifier):
    raw = payload()
    assert hook(client, raw).json() == {"status": "received"}
    assert hook(client, raw).json() == {"status": "duplicate"}  # ElevenLabs retry
    row = tools.log.get("conv_9")
    assert row["credits"] == 200 and row["call_successful"] == "success" and row["channel"] == "web"
    assert "agent: Hi, this is Cimo" in row["transcript"] and "[tool check_availability]" in row["transcript"]
    assert len(notifier.sent) == 1 and "Asked about parking." in notifier.sent[0][1]


def test_webhook_bad_signature(client):
    assert hook(client, payload(), secret="forged").status_code == 401


def test_webhook_missing_signature(client):
    assert client.post("/webhooks/elevenlabs", content=payload()).status_code == 401


def test_webhook_refused_without_secret(tmp_path, tools):
    c = TestClient(create_app(Settings(data_dir=tmp_path), tools))
    assert c.post("/webhooks/elevenlabs", content=payload()).status_code == 503


def test_webhook_ignores_unknown_shapes(client):
    raw = json.dumps({"type": "post_call_transcription", "data": {}})
    assert hook(client, raw).json() == {"status": "ignored"}


def test_audio_event_type_is_noop(client, notifier):
    raw = json.dumps({"type": "post_call_audio", "data": {"conversation_id": "c", "full_audio": "AAAA"}})
    assert hook(client, raw).json() == {"status": "received"} and notifier.sent == []


def test_initiation_failure_alerts(client, tools, alerts):
    raw = json.dumps({"type": "call_initiation_failure",
                      "data": {"agent_id": "a", "conversation_id": "cf1", "failure_reason": "busy"}})
    assert hook(client, raw).status_code == 200
    assert tools.log.get("cf1")["status"] == "initiation_failed"
    assert alerts.sent and "failed to start" in alerts.sent[0][0]


def test_webhook_processing_failure_lets_retry_through(client, tools, alerts, monkeypatch):
    calls = {"n": 0}
    real = tools.notifier.send

    def flaky(subject, body):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("smtp down")
        real(subject, body)
    monkeypatch.setattr(tools.notifier, "send", flaky)
    raw = payload(conv="conv_retry")
    assert hook(client, raw).status_code == 500
    assert alerts.sent and "webhook failed" in alerts.sent[0][0]
    assert hook(client, raw).json() == {"status": "received"}  # the retry is processed, not dropped


def test_budget_trip_from_webhooks_blocks_tools(client, tools, alerts):
    hook(client, payload(conv="a1", cost=600))
    hook(client, payload(conv="a2", cost=500))  # budget in the fixture is 1000
    r = tool_post(client, "book_appointment", {"name": "A", "phone": "1", "date": "2025-11-04", "time": "10:00"})
    assert "can't make bookings" in r.json()["result"]
    assert any("cost guard" in s for s, _ in alerts.sent)


def test_admin_views_need_token(client):
    for path in ("/calls", "/leads", "/guard", "/calendar.ics"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_admin_views_with_token(client):
    h = {"Authorization": "Bearer admin-xyz"}
    tool_post(client, "capture_lead", {"name": "Bo", "phone": "1", "interest": "whitening", "consent_to_contact": True,
                                       "conversation_id": "c7"})
    assert client.get("/leads", headers=h).json()[0]["name"] == "Bo"
    assert client.get("/guard", headers=h).json()["allowed"] is True
    assert client.get("/calendar.ics", headers=h).text.startswith("BEGIN:VCALENDAR")


def test_kill_switch_endpoints(client):
    h = {"Authorization": "Bearer admin-xyz"}
    assert client.post("/guard/pause", headers=h).json() == {"paused": True}
    r = tool_post(client, "check_availability", {})
    assert "can't make bookings" in r.json()["result"]
    assert client.post("/guard/resume", headers=h).json() == {"paused": False}
    assert tool_post(client, "check_availability", {}).json()["result"].startswith("Today is")
    assert client.post("/guard/pause").status_code == 401


def test_no_caller_details_in_logs(client, caplog):
    caplog.set_level("INFO")
    tool_post(client, "capture_lead", {"name": "Secret Name", "phone": "07999123456", "interest": "x",
                                       "consent_to_contact": True})
    assert "Secret Name" not in caplog.text and "07999123456" not in caplog.text


def test_conversation_id_cleaned(client, tools):
    tool_post(client, "handover", {"reason": "x", "urgent": False, "conversation_id": "c\x00" + "9" * 200})
    ids = [c["call_id"] for c in tools.log.calls()]
    assert all("\x00" not in i and len(i) <= 80 for i in ids)
