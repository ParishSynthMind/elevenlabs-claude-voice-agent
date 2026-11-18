import json

import httpx
import pytest

from voiceagent.elevenlabs_api import ElevenLabsClient, ElevenLabsError


class Fake:
    """A fake ElevenLabs at the HTTP layer: documented paths, status codes and error bodies."""

    def __init__(self):
        self.requests = []
        self.fail = {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = (request.method, request.url.path)
        if key in self.fail and self.fail[key]:
            status, body, headers = self.fail[key].pop(0)
            return httpx.Response(status, json=body, headers=headers or {})
        if request.headers.get("xi-api-key") != "k":
            return httpx.Response(401, json={"detail": {"status": "invalid_api_key", "message": "Invalid API key"}})
        p = request.url.path
        body = json.loads(request.content) if request.content else {}
        if p == "/v1/convai/secrets":
            return httpx.Response(200, json={"type": "stored", "secret_id": "sec_1", "name": body["name"]})
        if p == "/v1/convai/tools" and request.method == "POST":
            return httpx.Response(200, json={"id": "tool_1", "tool_config": body["tool_config"]})
        if p == "/v1/convai/knowledge-base/text":
            return httpx.Response(200, json={"id": "kb_1", "name": body["name"]})
        if p == "/v1/pronunciation-dictionaries/add-from-rules":
            return httpx.Response(200, json={"id": "d_1", "name": body["name"], "version_id": "v_1",
                                             "version_rules_num": len(body["rules"])})
        if p == "/v1/workspace/webhooks":
            return httpx.Response(200, json={"webhook_id": "wh_1", "webhook_secret": "wsec"})
        if p == "/v1/convai/agents/create":
            if "conversation_config" not in body:
                return httpx.Response(422, json={"detail": [{"loc": ["body", "conversation_config"],
                                                             "msg": "Field required", "type": "missing"}]})
            return httpx.Response(200, json={"agent_id": "agent_1"})
        if p == "/v1/convai/conversation/get-signed-url":
            return httpx.Response(200, json={"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?x=1"})
        if p == "/v1/user/subscription":
            return httpx.Response(200, json={"character_count": 100, "character_limit": 1000})
        if p.startswith("/v1/text-to-speech/"):
            return httpx.Response(200, content=b"ID3fakeaudio", headers={"content-type": "audio/mpeg",
                                                                         "request-id": "req_1"})
        if request.method in ("DELETE", "PATCH"):
            return httpx.Response(200, json={})
        if p.startswith("/v1/convai/agents/"):
            return httpx.Response(200, json={"agent_id": "agent_1", "platform_settings": {"call_limits": {}}})
        return httpx.Response(404, json={"detail": "Not Found"})


@pytest.fixture
def fake():
    return Fake()


@pytest.fixture
def api(fake):
    return ElevenLabsClient("k", transport=httpx.MockTransport(fake), sleep=lambda s: None)


def test_requires_key():
    with pytest.raises(ValueError):
        ElevenLabsClient("")


def test_create_calls(api, fake):
    assert api.create_secret("n", "v") == "sec_1"
    assert api.create_tool({"type": "webhook", "name": "x"}) == "tool_1"
    assert api.create_kb_text("FAQ", "text") == "kb_1"
    assert api.create_dictionary("D", [{"string_to_replace": "a", "type": "alias", "alias": "b"}]) == \
        {"pronunciation_dictionary_id": "d_1", "version_id": "v_1"}
    assert api.create_webhook("w", "https://x/y") == {"webhook_id": "wh_1", "webhook_secret": "wsec"}
    assert api.create_agent({"conversation_config": {}}) == "agent_1"
    assert all(r.headers["xi-api-key"] == "k" for r in fake.requests)


def test_webhook_body_is_hmac(api, fake):
    api.create_webhook("w", "https://x/y")
    sent = json.loads(fake.requests[-1].content)
    assert sent == {"settings": {"auth_type": "hmac", "name": "w", "webhook_url": "https://x/y"}}


def test_secret_body(api, fake):
    api.create_secret("n", "v")
    assert json.loads(fake.requests[-1].content) == {"type": "new", "name": "n", "value": "v"}


def test_validation_error_message(api):
    with pytest.raises(ElevenLabsError) as e:
        api.create_agent({})
    assert e.value.status == 422 and "conversation_config: Field required" in str(e.value)


def test_auth_error_message():
    c = ElevenLabsClient("bad", transport=httpx.MockTransport(Fake()), sleep=lambda s: None)
    with pytest.raises(ElevenLabsError) as e:
        c.subscription()
    assert e.value.status == 401 and "Invalid API key" in str(e.value)


def test_retries_429_with_retry_after(fake):
    waits = []
    c = ElevenLabsClient("k", transport=httpx.MockTransport(fake), sleep=waits.append)
    fake.fail[("GET", "/v1/user/subscription")] = [(429, {"detail": "rate"}, {"retry-after": "2"}),
                                                   (503, {"detail": "busy"}, None)]
    assert c.subscription()["character_count"] == 100
    assert waits == [2.0, 2]


def test_gives_up_after_max_retries(fake):
    c = ElevenLabsClient("k", transport=httpx.MockTransport(fake), sleep=lambda s: None, max_retries=2)
    fake.fail[("GET", "/v1/user/subscription")] = [(500, {"detail": "x"}, None)] * 3
    with pytest.raises(ElevenLabsError) as e:
        c.subscription()
    assert e.value.status == 500


def test_does_not_retry_4xx(fake):
    waits = []
    c = ElevenLabsClient("k", transport=httpx.MockTransport(fake), sleep=waits.append)
    fake.fail[("GET", "/v1/user/subscription")] = [(400, {"detail": "bad"}, None)]
    with pytest.raises(ElevenLabsError):
        c.subscription()
    assert waits == []


def test_retry_after_capped(fake):
    waits = []
    c = ElevenLabsClient("k", transport=httpx.MockTransport(fake), sleep=waits.append)
    fake.fail[("GET", "/v1/user/subscription")] = [(429, {}, {"retry-after": "3600"})]
    c.subscription()
    assert waits == [30.0]


def test_tts_request(api, fake):
    r = api.text_to_speech("voice1", "Hello", "eleven_flash_v2_5",
                           [{"pronunciation_dictionary_id": "d", "version_id": "v"}], zero_retention=True)
    req = fake.requests[-1]
    assert req.url.path == "/v1/text-to-speech/voice1"
    assert req.url.params["output_format"] == "mp3_44100_128" and req.url.params["enable_logging"] == "false"
    assert json.loads(req.content)["pronunciation_dictionary_locators"][0]["pronunciation_dictionary_id"] == "d"
    assert r.content == b"ID3fakeaudio"


def test_signed_url(api, fake):
    assert api.signed_url("agent_1").startswith("wss://")
    assert fake.requests[-1].url.params["agent_id"] == "agent_1"


def test_deletes_and_updates(api, fake):
    api.delete_agent("a")
    api.delete_tool("t")
    api.delete_kb_document("k")
    api.delete_webhook("w")
    api.delete_secret("s")
    api.update_tool("t", {"type": "webhook"})
    api.update_agent("a", {"name": "x"})
    paths = [(r.method, r.url.path) for r in fake.requests]
    assert ("DELETE", "/v1/convai/agents/a") in paths and ("PATCH", "/v1/convai/tools/t") in paths
    assert ("DELETE", "/v1/workspace/webhooks/w") in paths and ("DELETE", "/v1/convai/secrets/s") in paths
    kb = [r for r in fake.requests if r.url.path == "/v1/convai/knowledge-base/k"][0]
    assert kb.url.params["force"] == "true"
