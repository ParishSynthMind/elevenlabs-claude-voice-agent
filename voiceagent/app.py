"""FastAPI service ElevenLabs talks to.

Endpoints
- POST /tools/{name}            the four webhook tools (check_availability, book_appointment,
                                capture_lead, handover). Header X-Agent-Tool-Secret must match.
- POST /webhooks/elevenlabs     post-call webhooks (post_call_transcription, call_initiation_failure).
                                ElevenLabs-Signature must verify. Answers 200 fast; retries are no-ops.
- GET  /calls, /leads, /calendar.ics, /guard   admin views, bearer ADMIN_TOKEN.
- POST /guard/pause, /guard/resume             kill switch, bearer ADMIN_TOKEN.
- GET  /health

Without AGENT_TOOL_SECRET / ELEVENLABS_WEBHOOK_SECRET the service refuses that traffic (503) unless
ALLOW_UNSIGNED=1 is set for local development.
"""
from __future__ import annotations

import hmac
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
from zoneinfo import ZoneInfo

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .calendar.local import LocalCalendar
from .calllog import CallLog
from .config import Settings
from .costguard import CostGuard
from .heartbeat import default_alert
from .notify import Notifier, OutboxNotifier, SmtpNotifier
from .scheduling import Scheduler
from .signature import HEADER, TOOL_HEADER, tool_secret_ok, verify
from .tools import VoiceAgentTools, _clean

log = logging.getLogger("voiceagent")
MAX_BODY = 2 * 1024 * 1024  # transcripts are small; audio webhooks are not subscribed


def _alert(job: str) -> Callable[[str, str], None]:
    """Failure alerts go through the heartbeat template's alert route (webhook / email / stderr)."""
    def send(subject: str, detail: str) -> None:
        try:
            default_alert(job, "failure", subject, detail)
        except Exception:  # an alert must never break a call
            log.exception("alert failed")
    return send


def build_tools(settings: Settings, calendar=None, notifier: Optional[Notifier] = None,
                alert: Optional[Callable[[str, str], None]] = None) -> VoiceAgentTools:
    if calendar is None:
        if settings.calendar_backend == "google":
            from .calendar.google import GoogleCalendar, service_account_token_provider
            info = json.loads(settings.google_service_account_json) if settings.google_service_account_json else None
            calendar = GoogleCalendar(settings.google_calendar_id,
                                      service_account_token_provider(settings.google_service_account_file, info),
                                      timezone=settings.timezone)
        else:
            calendar = LocalCalendar(settings.data_dir / "calendar.sqlite3")
    if notifier is None:
        if settings.smtp_host and settings.team_email:
            notifier = SmtpNotifier(settings.smtp_host, settings.smtp_port, settings.smtp_user,
                                    settings.smtp_password, settings.smtp_from, settings.team_email)
        else:
            notifier = OutboxNotifier(settings.data_dir / "outbox.jsonl")
    alert = alert or _alert("ElevenLabs voice agent")
    calllog = CallLog(settings.data_dir / "calls.sqlite3")
    scheduler = Scheduler(calendar, settings.timezone, settings.hours, settings.slot_minutes,
                          settings.min_notice_minutes)
    guard = CostGuard(calllog, settings.timezone, settings.daily_credit_budget, settings.daily_call_limit,
                      settings.paused, alert)
    return VoiceAgentTools(scheduler, calendar, notifier, calllog, settings.business_name, guard,
                           settings.handover_notice, on_error=alert)


def _transcript_text(turns: Any) -> str:
    if not isinstance(turns, list):
        return ""
    lines = []
    for t in turns:
        if not isinstance(t, dict):
            continue
        msg = (t.get("message") or "").strip()
        if msg:
            lines.append(f"{t.get('role', '?')}: {msg}")
        for call in t.get("tool_calls") or []:
            if isinstance(call, dict):
                lines.append(f"  [tool {call.get('tool_name')}]")
    return "\n".join(lines)


def create_app(settings: Optional[Settings] = None, tools: Optional[VoiceAgentTools] = None) -> FastAPI:
    settings = settings or Settings.from_env()
    tl = tools or build_tools(settings)
    app = FastAPI(title="ElevenLabs Claude Voice Agent")
    app.state.tools = tl
    tz = ZoneInfo(settings.timezone)

    async def read_json(request: Request) -> tuple:
        raw = await request.body()
        if len(raw) > MAX_BODY:
            raise HTTPException(status_code=413, detail="body too large")
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text) if text else {}
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="body is not JSON") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        return text, body

    def check_admin(request: Request) -> None:
        given = request.headers.get("authorization", "")
        if not settings.admin_token or not hmac.compare_digest(given, f"Bearer {settings.admin_token}"):
            raise HTTPException(status_code=401, detail="Unauthorized")

    @app.get("/health")
    def health() -> Dict[str, str]:
        return {"status": "ok"}

    @app.post("/tools/{name}")
    async def tool(name: str, request: Request) -> Dict[str, str]:
        if settings.tool_secret:
            if not tool_secret_ok(settings.tool_secret, request.headers.get(TOOL_HEADER)):
                log.warning("rejected a tool call with a missing or wrong %s", TOOL_HEADER)
                raise HTTPException(status_code=401, detail="Unauthorized")
        elif not settings.allow_unsigned:
            raise HTTPException(status_code=503, detail="AGENT_TOOL_SECRET is not set")
        if name not in tl.handlers:
            raise HTTPException(status_code=404, detail=f"no tool called {name}")
        _, body = await read_json(request)
        call_id = _clean(body.pop("conversation_id", None), 80) or None
        ctx = {"id": call_id, "caller": None}
        result = tl.run(name, body, ctx)
        log.info("tool %s answered (%d characters)", name, len(result))  # no caller details in logs
        return {"result": result}

    @app.post("/webhooks/elevenlabs")
    async def webhook(request: Request) -> Dict[str, str]:
        text, body = await read_json(request)
        if settings.webhook_secret:
            if not verify(text, settings.webhook_secret, request.headers.get(HEADER)):
                log.warning("rejected a webhook with a missing, wrong or stale %s", HEADER)
                raise HTTPException(status_code=401, detail="Invalid signature")
        elif not settings.allow_unsigned:
            raise HTTPException(status_code=503, detail="ELEVENLABS_WEBHOOK_SECRET is not set")
        kind = body.get("type")
        data = body.get("data") if isinstance(body.get("data"), dict) else {}
        conv_id = _clean(data.get("conversation_id"), 80)
        if not kind or not conv_id:
            return {"status": "ignored"}
        if not tl.log.first_delivery(conv_id, kind):
            return {"status": "duplicate"}  # an ElevenLabs retry of something already handled
        try:
            handle_event(kind, conv_id, data)
        except Exception as e:
            tl.log.forget_delivery(conv_id, kind)  # let the retry try again
            if tl.on_error:
                tl.on_error("Voice agent webhook failed", f"{type(e).__name__}: {e} ({kind}, {conv_id})")
            raise HTTPException(status_code=500, detail="processing failed") from None
        return {"status": "received"}

    def handle_event(kind: str, conv_id: str, data: Dict[str, Any]) -> None:
        meta = data.get("metadata") or {}
        start = meta.get("start_time_unix_secs")
        started = datetime.fromtimestamp(int(start), tz=timezone.utc) if isinstance(start, (int, float)) else None
        day = (started or datetime.now(timezone.utc)).astimezone(tz).date().isoformat()
        phone = (meta.get("phone_call") or {}) if isinstance(meta.get("phone_call"), dict) else {}
        caller = _clean(phone.get("external_number"), 40) or None
        if kind == "call_initiation_failure":
            tl.log.mark(conv_id, agent_id=_clean(data.get("agent_id"), 80), day=day, status="initiation_failed",
                        termination_reason=_clean(data.get("failure_reason"), 200), caller=caller)
            if tl.on_error:
                tl.on_error("Voice agent call failed to start", f"{conv_id}: {data.get('failure_reason')}")
            return
        if kind != "post_call_transcription":
            return
        analysis = data.get("analysis") or {}
        credits = meta.get("cost")
        tl.log.mark(conv_id,
                    agent_id=_clean(data.get("agent_id"), 80), caller=caller, day=day,
                    channel="phone" if phone else "web",
                    started_at=started.isoformat() if started else None,
                    duration_seconds=meta.get("call_duration_secs"),
                    credits=int(credits) if isinstance(credits, (int, float)) else None,
                    status=_clean(data.get("status"), 40),
                    termination_reason=_clean(meta.get("termination_reason"), 200),
                    call_successful=_clean(analysis.get("call_successful"), 20),
                    analysis_summary=_clean(analysis.get("transcript_summary"), 2000),
                    transcript=_transcript_text(data.get("transcript")))
        tl.guard.check()  # trips (and alerts once) if today's credits passed the budget
        row = tl.log.get(conv_id) or {}
        if not row.get("summary_emailed"):
            body_text = (f"Call summary from the {settings.business_name} voice agent (ElevenLabs)\n\n"
                         f"Caller: {caller or 'web call'}\nConversation: {conv_id}\n"
                         f"Length: {meta.get('call_duration_secs')} s, credits: {credits}\n"
                         f"Ended: {meta.get('termination_reason') or 'n/a'}\n"
                         f"Result: {analysis.get('call_successful') or 'n/a'}\n\n"
                         f"What happened:\n{_clean(analysis.get('transcript_summary') or 'No summary.', 1500)}\n\n"
                         f"Actions taken: {row.get('outcome') or 'none'}\n")
            tl.notifier.send(f"Call summary: {caller or 'web call'}", body_text)
            tl.log.mark(conv_id, summary_emailed=1)

    @app.get("/calls")
    def calls(request: Request, limit: int = 20):
        check_admin(request)
        return tl.log.calls(min(max(limit, 1), 200))

    @app.get("/leads")
    def leads(request: Request, limit: int = 50):
        check_admin(request)
        return tl.log.leads(min(max(limit, 1), 500))

    @app.get("/guard")
    def guard(request: Request):
        check_admin(request)
        st = tl.guard.state()
        return {"allowed": st.allowed, "reason": st.reason, "credits_today": st.credits_today,
                "calls_today": st.calls_today, "daily_credit_budget": tl.guard.daily_credit_budget}

    @app.post("/guard/pause")
    def pause(request: Request):
        check_admin(request)
        tl.guard.pause()
        return {"paused": True}

    @app.post("/guard/resume")
    def resume(request: Request):
        check_admin(request)
        tl.guard.resume()
        return {"paused": False}

    @app.get("/calendar.ics", response_class=PlainTextResponse)
    def ics(request: Request):
        check_admin(request)
        export = getattr(tl.calendar, "export_ics", None)
        if export is None:
            raise HTTPException(status_code=404, detail="calendar backend has no .ics export")
        return PlainTextResponse(export(settings.business_name), media_type="text/calendar")

    return app
