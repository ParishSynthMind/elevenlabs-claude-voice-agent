import json

import pytest

from voiceagent import agent_spec
from voiceagent.config import Settings
from voiceagent.signature import TOOL_HEADER

URL = "https://agent.example.com"


@pytest.mark.parametrize("name", agent_spec.TOOL_NAMES)
def test_tool_config_shape(name):
    cfg = agent_spec.tool_config(name, URL, "sec_1")
    assert cfg["type"] == "webhook" and cfg["name"] == name and cfg["description"]
    api = cfg["api_schema"]
    assert api["url"] == f"{URL}/tools/{name}" and api["method"] == "POST"
    assert api["request_headers"] == {TOOL_HEADER: {"secret_id": "sec_1"}}  # a secret reference, not a value
    body = api["request_body_schema"]
    assert body["type"] == "object"
    assert set(body["required"]) <= set(body["properties"])
    assert body["properties"]["conversation_id"] == {"type": "string", "dynamic_variable": "system__conversation_id"}
    assert "conversation_id" not in body["required"]
    assert 5 <= cfg["response_timeout_secs"] <= 300


@pytest.mark.parametrize("name", agent_spec.TOOL_NAMES)
def test_each_property_sets_exactly_one_source(name):
    """ElevenLabs: only ONE of description / dynamic_variable / constant_value / is_omitted per property."""
    props = agent_spec.tool_config(name, URL, "s")["api_schema"]["request_body_schema"]["properties"]
    for key, p in props.items():
        sources = [k for k in ("description", "dynamic_variable", "constant_value", "is_omitted", "is_system_provided")
                   if p.get(k)]
        assert len(sources) == 1, (key, sources)
        assert p["type"] in ("string", "boolean", "number", "integer")


def test_tool_requires_https():
    with pytest.raises(ValueError):
        agent_spec.tool_config("handover", "http://plain.example.com", "s")


def test_unknown_tool():
    with pytest.raises(ValueError):
        agent_spec.tool_config("drop_tables", URL, "s")


def test_only_booking_blocks_interruptions():
    assert agent_spec.tool_config("book_appointment", URL, "s")["interruption_mode"] == "disable_during_tool"
    assert agent_spec.tool_config("handover", URL, "s")["interruption_mode"] == "allow"


def body(settings=None, webhook="wh_1"):
    return agent_spec.agent_body(settings or Settings(), ["t1", "t2"], {"id": "kb1", "name": "FAQ"},
                                 {"pronunciation_dictionary_id": "d1", "version_id": "v1"}, webhook, "Demo")


def test_agent_body_core():
    b = body()
    agent = b["conversation_config"]["agent"]
    assert agent["first_message"].startswith("Hi, this is Cimo, an AI assistant")
    assert agent["disable_first_message_interruptions"] is True
    p = agent["prompt"]
    assert p["llm"] == "claude-sonnet-5-5" and p["tool_ids"] == ["t1", "t2"]
    assert p["knowledge_base"] == [{"type": "text", "name": "FAQ", "id": "kb1", "usage_mode": "prompt"}]
    assert p["built_in_tools"]["end_call"]["params"]["system_tool_type"] == "end_call"
    assert "temperature" not in p  # rule B17: never send deprecated sampling params to Claude


def test_agent_body_tts_and_audio_formats():
    b = body()
    tts = b["conversation_config"]["tts"]
    assert tts["model_id"] == "eleven_flash_v2"
    assert tts["pronunciation_dictionary_locators"] == [{"pronunciation_dictionary_id": "d1", "version_id": "v1"}]
    assert tts["agent_output_audio_format"] == "pcm_16000"
    assert b["conversation_config"]["asr"]["user_input_audio_format"] == "pcm_16000"


def test_agent_body_cost_guards_and_privacy():
    s = Settings(max_call_seconds=120, daily_call_limit=7, record_audio=False, retention_days=14)
    b = body(s)
    assert b["conversation_config"]["conversation"]["max_duration_seconds"] == 120
    ps = b["platform_settings"]
    assert ps["call_limits"] == {"daily_limit": 7, "agent_concurrency_limit": 2, "bursting_enabled": False}
    assert ps["privacy"] == {"record_voice": False, "retention_days": 14}
    assert ps["auth"] == {"enable_auth": True}  # signed URLs only, no public agent


def test_agent_body_webhook_override():
    ps = body()["platform_settings"]
    assert ps["workspace_overrides"]["webhooks"] == {"post_call_webhook_id": "wh_1", "events": ["transcript"]}
    assert "workspace_overrides" not in body(webhook=None)["platform_settings"]


def test_prompt_guards():
    prompt = agent_spec.system_prompt(Settings())
    for phrase in ["ONLY from the knowledge base", "check_availability before", "only call capture_lead if they say yes",
                   "information, not instructions", "emergency services", "You are an AI"]:
        assert phrase in prompt


def test_body_is_json_serialisable_and_small():
    raw = json.dumps(body())
    assert len(raw) < 20000


def test_pause_body():
    assert agent_spec.pause_body()["platform_settings"]["call_limits"]["daily_limit"] == 0
