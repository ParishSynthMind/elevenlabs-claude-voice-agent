import time

import pytest

from voiceagent.signature import sign, tool_secret_ok, verify

BODY = '{"type":"post_call_transcription","data":{"conversation_id":"c1"}}'
SECRET = "wsec_test"


def test_round_trip():
    assert verify(BODY, SECRET, sign(BODY, SECRET))


def test_matches_elevenlabs_sdk():
    """Cross-check against ElevenLabs' own Python SDK (webhooks.construct_event)."""
    pytest.importorskip("elevenlabs")
    from elevenlabs.client import ElevenLabs
    from elevenlabs.webhooks_custom import WebhooksClient  # noqa: F401  (import path check)
    header = sign(BODY, SECRET)
    event = ElevenLabs(api_key="unused").webhooks.construct_event(rawBody=BODY, sig_header=header, secret=SECRET)
    assert event["data"]["conversation_id"] == "c1"


def test_sdk_rejects_what_we_reject():
    pytest.importorskip("elevenlabs")
    from elevenlabs.client import ElevenLabs
    from elevenlabs.errors import BadRequestError
    header = sign(BODY, "other-secret")
    assert not verify(BODY, SECRET, header)
    with pytest.raises(BadRequestError):
        ElevenLabs(api_key="unused").webhooks.construct_event(rawBody=BODY, sig_header=header, secret=SECRET)


def test_changed_body_fails():
    header = sign(BODY, SECRET)
    assert not verify(BODY.replace("c1", "c2"), SECRET, header)


def test_wrong_secret_fails():
    assert not verify(BODY, "nope", sign(BODY, SECRET))


@pytest.mark.parametrize("header", [None, "", "garbage", "t=,v0=", "v0=abc", "t=123", "t=abc,v0=00"])
def test_malformed_headers(header):
    assert not verify(BODY, SECRET, header)


def test_missing_secret_fails():
    assert not verify(BODY, None, sign(BODY, SECRET))
    assert not verify(BODY, "", sign(BODY, SECRET))


def test_stale_timestamp_refused():
    old = int(time.time()) - 31 * 60
    assert not verify(BODY, SECRET, sign(BODY, SECRET, old))


def test_future_timestamp_refused():
    future = int(time.time()) + 31 * 60
    assert not verify(BODY, SECRET, sign(BODY, SECRET, future))


def test_inside_window_accepted():
    t = int(time.time()) - 29 * 60
    assert verify(BODY, SECRET, sign(BODY, SECRET, t))


def test_extra_parts_and_multiple_v0():
    good = sign(BODY, SECRET)
    t, v0 = good.split(",")
    assert verify(BODY, SECRET, f"{t},v0=deadbeef,{v0},v1=ignored")


def test_raw_body_not_reserialised():
    spaced = BODY.replace(":", ": ")
    assert not verify(spaced, SECRET, sign(BODY, SECRET))


@pytest.mark.parametrize("expected,given,ok", [
    ("abc", "abc", True), ("abc", "abd", False), ("abc", None, False), (None, "abc", False),
    ("", "", False), ("abc", "abcd", False),
])
def test_tool_secret(expected, given, ok):
    assert tool_secret_ok(expected, given) is ok
