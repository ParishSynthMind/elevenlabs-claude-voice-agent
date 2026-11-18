#!/usr/bin/env python3
"""Create, update, pause, resume or delete the whole ElevenLabs setup for this voice agent.

  python scripts/setup_agent.py create --server-url https://your-server.example.com [--dry-run]
  python scripts/setup_agent.py update --server-url https://new-url.example.com
  python scripts/setup_agent.py update --rotate-secret   # new tool secret on ElevenLabs + Keychain
  python scripts/setup_agent.py pause      # kill switch on ElevenLabs' side (no new calls)
  python scripts/setup_agent.py resume
  python scripts/setup_agent.py status
  python scripts/setup_agent.py delete     # agent, tools, knowledge base, webhook, secret

`create` makes, in order: a workspace secret (the tool-call header), the four webhook tools, the
knowledge-base text document, the pronunciation dictionary, an HMAC post-call webhook and the agent.
IDs go to var/elevenlabs-state.json (IDs only). The two generated secrets go to the Keychain
(agent-tool-secret, elevenlabs-webhook-secret) or, off macOS, must be set as AGENT_TOOL_SECRET and
ELEVENLABS_WEBHOOK_SECRET in your own secret store: they are never printed or written to a file.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from voiceagent import agent_spec, pronunciation, secrets_store  # noqa: E402
from voiceagent.config import Settings  # noqa: E402
from voiceagent.elevenlabs_api import ElevenLabsClient, ElevenLabsError  # noqa: E402

AGENT_NAME = "Harbour Dental voice agent (Claude, portfolio demo)"


def state_path(settings: Settings) -> Path:
    return settings.data_dir / "elevenlabs-state.json"


def load_state(settings: Settings) -> dict:
    p = state_path(settings)
    return json.loads(p.read_text()) if p.exists() else {}


def save_state(settings: Settings, state: dict) -> None:
    p = state_path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2))


def client_from_env(keychain: str) -> ElevenLabsClient:
    key = secrets_store.get("ELEVENLABS_API_KEY", keychain)
    if not key:
        sys.exit("No ElevenLabs key: set ELEVENLABS_API_KEY or store it in the Keychain.")
    return ElevenLabsClient(key)


def create(args, settings: Settings) -> int:
    dictionary_rules = pronunciation.load(settings.pronunciation_file)
    kb_text = settings.knowledge_base.read_text(encoding="utf-8")
    preview_tools = [agent_spec.tool_config(n, args.server_url, "SECRET_ID") for n in agent_spec.TOOL_NAMES]
    preview_agent = agent_spec.agent_body(settings, ["TOOL_IDS"], {"id": "KB_ID", "name": "Clinic FAQ"},
                                          {"pronunciation_dictionary_id": "DICT_ID", "version_id": "VERSION"},
                                          "WEBHOOK_ID", AGENT_NAME)
    if args.dry_run:
        print(json.dumps({"tools": preview_tools, "agent": preview_agent,
                          "dictionary_rules": dictionary_rules["rules"]}, indent=2))
        return 0
    state = load_state(settings)
    if state.get("agent_id"):
        sys.exit(f"Already created (agent {state['agent_id']}). Use update or delete first.")
    if state:
        print("Resuming a create that stopped part-way (IDs already made are reused).")
    c = client_from_env(args.keychain)
    stored = True
    if not state.get("secret_id"):
        tool_secret = secrets.token_urlsafe(32)
        state["secret_id"] = c.create_secret("voice-agent-tool-secret", tool_secret)
        stored = secrets_store.put("agent-tool-secret", tool_secret)
        save_state(settings, state)
    state.setdefault("tool_ids", {})
    for name in agent_spec.TOOL_NAMES:
        if name not in state["tool_ids"]:
            state["tool_ids"][name] = c.create_tool(agent_spec.tool_config(name, args.server_url, state["secret_id"]))
            save_state(settings, state)
    if not state.get("kb"):
        state["kb"] = {"id": c.create_kb_text("Harbour Dental Clinic FAQ", kb_text), "name": "Harbour Dental Clinic FAQ"}
        save_state(settings, state)
    if not state.get("dictionary"):
        state["dictionary"] = c.create_dictionary(dictionary_rules["name"], dictionary_rules["rules"],
                                                  dictionary_rules.get("description", ""))
        save_state(settings, state)
    hook = {}
    if not state.get("webhook_id"):
        hook = c.create_webhook("voice-agent post-call", args.server_url.rstrip("/") + "/webhooks/elevenlabs")
        state["webhook_id"] = hook["webhook_id"]
        if hook.get("webhook_secret"):
            secrets_store.put("elevenlabs-webhook-secret", hook["webhook_secret"])
        save_state(settings, state)
    body = agent_spec.agent_body(settings, list(state["tool_ids"].values()), state["kb"], state["dictionary"],
                                 state["webhook_id"], AGENT_NAME)
    state["agent_id"] = c.create_agent(body)
    state["server_url"] = args.server_url
    save_state(settings, state)
    print(f"Created agent {state['agent_id']} with {len(state['tool_ids'])} tools.")
    print("Tool secret stored in the Keychain (agent-tool-secret)." if stored else
          "Set AGENT_TOOL_SECRET on your server: it was generated now and is not shown; re-run on macOS or "
          "rotate it with update --rotate-secret (see its help).")
    if hook:
        print("Webhook secret stored in the Keychain (elevenlabs-webhook-secret)." if hook.get("webhook_secret")
              else "ElevenLabs returned no webhook secret: copy it from Developers > Webhooks.")
    return 0


def update(args, settings: Settings) -> int:
    state = load_state(settings)
    if not state.get("agent_id"):
        sys.exit("Nothing created yet.")
    c = client_from_env(args.keychain)
    url = args.server_url or state["server_url"]
    if getattr(args, "rotate_secret", False):
        # Off macOS there is no Keychain: the operator puts the new value in their own secret store first and
        # passes it as NEW_AGENT_TOOL_SECRET. It is never printed.
        tool_secret = os.environ.get("NEW_AGENT_TOOL_SECRET") or secrets.token_urlsafe(32)
        if not os.environ.get("NEW_AGENT_TOOL_SECRET") and not secrets_store.put("agent-tool-secret", tool_secret):
            sys.exit("No Keychain here: set NEW_AGENT_TOOL_SECRET (and AGENT_TOOL_SECRET on the server) to a new "
                     "random value, then run update --rotate-secret again.")
        c.update_secret(state["secret_id"], "voice-agent-tool-secret", tool_secret)
        print("Tool secret rotated on ElevenLabs. Restart the server so it loads the new AGENT_TOOL_SECRET.")
    for name, tid in state["tool_ids"].items():
        c.update_tool(tid, agent_spec.tool_config(name, url, state["secret_id"]))
    body = agent_spec.agent_body(settings, list(state["tool_ids"].values()), state.get("kb"), state.get("dictionary"),
                                 state.get("webhook_id"), AGENT_NAME)
    c.update_agent(state["agent_id"], body)
    state["server_url"] = url
    save_state(settings, state)
    print(f"Updated agent {state['agent_id']} and {len(state['tool_ids'])} tools (server {url}).")
    print("Note: the post-call webhook URL is set when it is created; delete and create to move it.")
    return 0


def pause(args, settings: Settings) -> int:
    state = load_state(settings)
    c = client_from_env(args.keychain)
    c.update_agent(state["agent_id"], agent_spec.pause_body())
    print("Paused: the agent accepts no new conversations. Resume with: resume")
    return 0


def resume(args, settings: Settings) -> int:
    return update(argparse.Namespace(keychain=args.keychain, server_url=None, rotate_secret=False), settings)


def status(args, settings: Settings) -> int:
    state = load_state(settings)
    c = client_from_env(args.keychain)
    sub = c.subscription()
    print(json.dumps({"state": state, "credits_used": sub.get("character_count"),
                      "credit_limit": sub.get("character_limit")}, indent=2))
    if state.get("agent_id"):
        limits = c.get_agent(state["agent_id"]).get("platform_settings", {}).get("call_limits")
        print("call_limits:", limits)
    return 0


def delete(args, settings: Settings) -> int:
    state = load_state(settings)
    c = client_from_env(args.keychain)

    def attempt(label, fn, *a):
        try:
            fn(*a)
            print(f"deleted {label}")
        except ElevenLabsError as e:
            print(f"could not delete {label}: {e}")

    if state.get("agent_id"):
        attempt("agent", c.delete_agent, state["agent_id"])
    for name, tid in (state.get("tool_ids") or {}).items():
        attempt(f"tool {name}", c.delete_tool, tid)
    if state.get("kb"):
        attempt("knowledge base document", c.delete_kb_document, state["kb"]["id"])
    if state.get("webhook_id"):
        attempt("webhook", c.delete_webhook, state["webhook_id"])
    if state.get("secret_id"):
        attempt("secret", c.delete_secret, state["secret_id"])
    if state.get("dictionary"):
        print(f"kept pronunciation dictionary {state['dictionary']['pronunciation_dictionary_id']} "
              "(reusable; archive it in the dashboard if not needed)")
    state_path(settings).unlink(missing_ok=True)
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["create", "update", "pause", "resume", "status", "delete"])
    ap.add_argument("--server-url")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--rotate-secret", action="store_true", help="update: new tool secret on ElevenLabs and here")
    ap.add_argument("--keychain", default="elevenlabs-api-key", help="Keychain service holding the API key")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    if args.command == "create" and not args.server_url:
        ap.error("create needs --server-url")
    return {"create": create, "update": update, "pause": pause, "resume": resume, "status": status,
            "delete": delete}[args.command](args, settings)


if __name__ == "__main__":
    raise SystemExit(main())
