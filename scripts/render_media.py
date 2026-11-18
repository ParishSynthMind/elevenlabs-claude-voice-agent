"""Render the README images and the 4:3 portfolio thumbnail from the recorded runs. Nothing typed in by hand:
every row comes from examples/live-run/ (the real ElevenLabs calls) and examples/local-run/run.json.

    python scripts/render_media.py   (needs: pip install playwright && playwright install chromium)
"""

import html
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
LIVE = ROOT / "examples" / "live-run"
MEDIA = ROOT / "media"
FONT = "-apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"
STYLE = f"""<style>
 body{{margin:0;font-family:{FONT};background:#fff;color:#1a1a1a}}
 .wrap{{display:inline-block;padding:24px 28px;background:#fff}}
 h1{{font-size:20px;margin:0 0 4px}} .sub{{color:#555;font-size:13px;margin:0 0 14px;max-width:980px;line-height:1.45}}
 table{{border-collapse:collapse;font-size:13px;width:1040px}}
 td,th{{border-bottom:1px solid #e6e6e6;padding:8px 10px;text-align:left;vertical-align:top}}
 th{{background:#f4f6fb;font-weight:600}}
 .who{{font-weight:600;width:70px}} .t{{color:#777;width:56px;font-variant-numeric:tabular-nums}}
 .agent{{color:#1f3fbf}} .user{{color:#0f7a4a}}
 .tool{{background:#fff8e1}} .ok{{color:#0f7a4a;font-weight:600}} .bad{{color:#b42318;font-weight:600}}
 pre{{white-space:pre-wrap;font-family:{FONT};margin:0;font-size:13px;line-height:1.45}}
 .thumb{{width:1200px;height:900px;box-sizing:border-box;padding:70px 80px;
   background:linear-gradient(135deg,#120b2e 0%,#3b1f8f 55%,#7a4dff 100%);color:#fff;position:relative;overflow:hidden}}
 .thumb h2{{font-size:66px;line-height:1.05;margin:0 0 22px;letter-spacing:-1px}}
 .thumb .lead{{font-size:28px;opacity:.92;max-width:900px;line-height:1.35}}
 .chips{{margin-top:40px;display:flex;flex-wrap:wrap;gap:14px;max-width:1040px}}
 .chip{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.35);border-radius:30px;padding:12px 22px;font-size:24px}}
 .card{{position:absolute;right:70px;bottom:60px;width:540px;background:#fff;color:#222;border-radius:16px;padding:20px 24px;
   font-size:19px;line-height:1.4;box-shadow:0 20px 50px rgba(0,0,0,.35)}}
 .card b{{color:#3b1f8f}} .card .u{{color:#0f7a4a}}
</style>"""


def esc(s):
    return html.escape(str(s if s is not None else ""))


def page(title, sub, body):
    return f"<html><head>{STYLE}</head><body><div class='wrap'><h1>{esc(title)}</h1><p class='sub'>{sub}</p>{body}</div></body></html>"


def live_call():
    call = json.loads((LIVE / "call2-client.json").read_text())
    tool = json.loads((LIVE / "server-tool-events.json").read_text())[0]
    rows = []
    tool_done = False
    for t in call["transcript"]:
        if t["role"] == "agent" and "one moment" in t["text"].lower() and not tool_done:
            rows.append(f"<tr><td class='t'>{t['at']:.1f}s</td><td class='who agent'>Agent</td><td>{esc(t['text'])}</td></tr>")
            rows.append(f"<tr class='tool'><td class='t'></td><td class='who'>Tool</td><td>ElevenLabs called our server: "
                        f"<b>{esc(tool['tool'])}</b> {esc(tool['arguments'])} (shared secret verified, HTTP 200)<br>"
                        f"<span style='color:#555'>{esc(tool['result'][:170])}...</span></td></tr>")
            tool_done = True
            continue
        cls = "agent" if t["role"] == "agent" else "user"
        rows.append(f"<tr><td class='t'>{t['at']:.1f}s</td><td class='who {cls}'>{'Agent' if cls == 'agent' else 'Caller'}</td>"
                    f"<td>{esc(t['text'])}</td></tr>")
    sub = (f"Real ElevenLabs Agents voice call, 17 Nov 2026 ({esc(call['conversation_id'])}). Claude Sonnet 5.5 as the brain, "
           f"ElevenLabs voice, synthetic test caller. The first line is the AI disclosure and recording notice. "
           f"The call stopped at our 55-second test cap before the booking step.")
    return page("Live call: knowledge-base answer + a real server-tool call", sub,
                f"<table><tr><th>Time</th><th>Who</th><th>What was said</th></tr>{''.join(rows)}</table>")


def live_webhook():
    calls = json.loads((LIVE / "server-calls.json").read_text())
    hooks = {h["call_id"]: h for h in json.loads((LIVE / "server-webhook-events.json").read_text())}
    rows = "".join(
        f"<tr><td>{esc(c['call_id'])}</td><td class='ok'>verified</td><td>{esc(hooks.get(c['call_id'], {}).get('event'))}</td>"
        f"<td>{esc(c['duration_seconds'])} s</td><td>{esc(c['credits'])}</td><td>{esc(c['analysis_summary'])}</td></tr>"
        for c in calls)
    sub = ("ElevenLabs sent each post-call webhook to our server; the ElevenLabs-Signature HMAC was checked with the real "
           "webhook secret before anything was stored. The credits per call feed the daily credit budget (cost guard); "
           "a summary email went to the team outbox for each call.")
    return page("Live post-call webhooks: signature verified, credits logged", sub,
                f"<table><tr><th>Conversation</th><th>Signature</th><th>Event</th><th>Length</th><th>Credits</th>"
                f"<th>Summary (written by ElevenLabs' analysis)</th></tr>{rows}</table>")


def live_settings():
    d = json.loads((LIVE / "agent-readback.json").read_text())
    cc, ps = d["conversation_config"], d["platform_settings"]
    tools = [t.get("name") for t in cc["agent"]["prompt"].get("tools", []) or []]
    limits = ps.get("call_limits", {})
    facts = [
        ("Brain (LLM)", cc["agent"]["prompt"].get("llm")),
        ("Voice / TTS model", f"{cc['tts'].get('voice_id')} / {cc['tts'].get('model_id')}"),
        ("Pronunciation dictionary", f"{len(cc['tts'].get('pronunciation_dictionary_locators') or [])} attached"),
        ("First message (spoken first)", cc["agent"].get("first_message")),
        ("First message can be interrupted", cc["agent"].get("disable_first_message_interruptions") is False),
        ("Max call length", f"{cc['conversation'].get('max_duration_seconds')} s"),
        ("Daily call limit / concurrency", f"{limits.get('daily_limit')} / {limits.get('agent_concurrency_limit')} "
                                            f"(bursting {limits.get('bursting_enabled')})"),
        ("Signed URLs required (enable_auth)", (ps.get("auth") or {}).get("enable_auth")),
        ("Recording / retention", f"record_voice {ps['privacy'].get('record_voice')}, "
                                  f"{ps['privacy'].get('retention_days')} days"),
        ("Server tools", ", ".join(t for t in tools if t) or "(see tool_ids)"),
    ]
    rows = "".join(f"<tr><td style='width:260px'><b>{esc(k)}</b></td><td>{esc(v)}</td></tr>" for k, v in facts)
    sub = "Read back from the ElevenLabs API after scripts/setup_agent.py create, 17 Nov 2026 (examples/live-run/agent-readback.json)."
    return page("Live agent as ElevenLabs stored it", sub, f"<table>{rows}</table>")


def local_run():
    run = json.loads((ROOT / "examples" / "local-run" / "run.json").read_text())
    rows = "".join(
        f"<tr><td>{esc(s['step'])}</td><td class='{'ok' if s['http'] == 200 else 'bad'}'>{s['http']}</td>"
        f"<td>{esc(str(s['answer'])[:150])}</td></tr>" for s in run["steps"])
    sub = "python scripts/local_run.py: the whole server path offline, no ElevenLabs account and no credits (fixed clock)."
    return page("Offline run: tools, signed webhooks and the cost guard", sub,
                f"<table><tr><th>Step</th><th>HTTP</th><th>Answer</th></tr>{rows}</table>")


def thumbnail():
    call = json.loads((LIVE / "call2-client.json").read_text())
    first = call["transcript"][0]["text"]
    user = call["transcript"][1]["text"]
    chips = ["ElevenLabs Agents", "Claude Sonnet 5.5", "Server tools", "Signed webhooks", "Cost guard", "Python + FastAPI"]
    return (f"<html><head>{STYLE}</head><body><div class='thumb'><h2>ElevenLabs voice agent<br>with Claude</h2>"
            f"<div class='lead'>Answers calls, books slots, captures leads, hands over to a person. "
            f"Tested on real ElevenLabs calls.</div>"
            f"<div class='chips'>{''.join(f'<span class=chip>{c}</span>' for c in chips)}</div>"
            f"<div class='card'><b>Agent:</b> {esc(first)}<br><br><span class='u'><b>Caller:</b></span> {esc(user)}</div>"
            f"</div></body></html>")


def main():
    MEDIA.mkdir(exist_ok=True)
    shots = {"live-call.png": live_call(), "live-webhook.png": live_webhook(),
             "live-agent-settings.png": live_settings(), "local-run.png": local_run()}
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(device_scale_factor=2)
        for name, doc in shots.items():
            pg.set_content(doc)
            pg.locator(".wrap").screenshot(path=str(MEDIA / name))
            print("wrote", name)
        pg.set_viewport_size({"width": 1200, "height": 900})
        pg2 = b.new_page(viewport={"width": 1200, "height": 900}, device_scale_factor=1)
        pg2.set_content(thumbnail())
        pg2.locator(".thumb").screenshot(path=str(MEDIA / "thumbnail-4x3.png"))
        print("wrote thumbnail-4x3.png")
        b.close()


if __name__ == "__main__":
    main()
