import importlib.util
import json
from pathlib import Path

import httpx
import pytest

from voiceagent import elevenlabs_api

from .test_api import Fake

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "setup_agent.py"


@pytest.fixture
def setup(monkeypatch, tmp_path):
    spec = importlib.util.spec_from_file_location("setup_agent", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    fake = Fake()
    stored = {}
    real = elevenlabs_api.ElevenLabsClient

    def client(key, **kw):
        return real("k", transport=httpx.MockTransport(fake), sleep=lambda s: None)
    monkeypatch.setattr(mod, "ElevenLabsClient", client)
    monkeypatch.setattr(mod.secrets_store, "put", lambda name, value, account="voiceagent": stored.setdefault(name, value) and True)
    monkeypatch.setenv("ELEVENLABS_API_KEY", "k")
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    return mod, fake, stored, tmp_path


def test_dry_run_sends_nothing(setup, capsys):
    mod, fake, _, _ = setup
    assert mod.main(["create", "--server-url", "https://x.example.com", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["tools"]) == 4 and fake.requests == []


def test_create_records_ids_and_keeps_secrets_out_of_state(setup):
    mod, fake, stored, tmp = setup
    assert mod.main(["create", "--server-url", "https://x.example.com"]) == 0
    state = json.loads((tmp / "elevenlabs-state.json").read_text())
    assert state["agent_id"] == "agent_1" and set(state["tool_ids"]) == {"check_availability", "book_appointment",
                                                                        "capture_lead", "handover"}
    assert state["webhook_id"] == "wh_1" and state["dictionary"]["version_id"] == "v_1"
    raw = (tmp / "elevenlabs-state.json").read_text()
    assert stored["agent-tool-secret"] not in raw and "wsec" not in raw
    assert stored["elevenlabs-webhook-secret"] == "wsec"
    # the secret value goes to ElevenLabs' secret store, and tools reference it by id only
    tool_bodies = [json.loads(r.content) for r in fake.requests if r.url.path == "/v1/convai/tools"]
    assert all(b["tool_config"]["api_schema"]["request_headers"]["X-Agent-Tool-Secret"] == {"secret_id": "sec_1"}
               for b in tool_bodies)


def test_create_twice_refused(setup):
    mod, *_ = setup
    mod.main(["create", "--server-url", "https://x.example.com"])
    with pytest.raises(SystemExit):
        mod.main(["create", "--server-url", "https://x.example.com"])


def test_update_pause_delete(setup, capsys):
    mod, fake, _, tmp = setup
    mod.main(["create", "--server-url", "https://x.example.com"])
    assert mod.main(["update", "--server-url", "https://y.example.com"]) == 0
    patched = [json.loads(r.content) for r in fake.requests if r.method == "PATCH" and "/tools/" in r.url.path]
    assert all(b["tool_config"]["api_schema"]["url"].startswith("https://y.example.com/tools/") for b in patched)
    assert mod.main(["pause"]) == 0
    pause = [json.loads(r.content) for r in fake.requests if r.method == "PATCH" and "/agents/" in r.url.path][-1]
    assert pause["platform_settings"]["call_limits"]["daily_limit"] == 0
    assert mod.main(["delete"]) == 0
    deleted = {r.url.path for r in fake.requests if r.method == "DELETE"}
    assert "/v1/convai/agents/agent_1" in deleted and "/v1/workspace/webhooks/wh_1" in deleted
    assert not (tmp / "elevenlabs-state.json").exists()


def test_create_needs_server_url(setup):
    mod, *_ = setup
    with pytest.raises(SystemExit):
        mod.main(["create"])


def test_create_resumes_after_a_failure(setup):
    mod, fake, _, tmp = setup
    fake.fail[("POST", "/v1/convai/agents/create")] = [(400, {"detail": {"message": "English Agents must use turbo or flash v2"}}, None)]
    with pytest.raises(mod.ElevenLabsError):
        mod.main(["create", "--server-url", "https://x.example.com"])
    partial = json.loads((tmp / "elevenlabs-state.json").read_text())
    assert "agent_id" not in partial and partial["webhook_id"] == "wh_1"
    before = len([r for r in fake.requests if r.url.path == "/v1/convai/tools"])
    assert mod.main(["create", "--server-url", "https://x.example.com"]) == 0
    after = len([r for r in fake.requests if r.url.path == "/v1/convai/tools"])
    assert after == before  # nothing created twice
    assert json.loads((tmp / "elevenlabs-state.json").read_text())["agent_id"] == "agent_1"
