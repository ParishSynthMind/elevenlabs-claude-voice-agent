import base64
import json

from voiceagent.callclient import CHUNK_BYTES, SILENCE, CallScript


def audio(n=CHUNK_BYTES):
    return json.dumps({"type": "audio", "audio_event": {"audio_base_64": base64.b64encode(b"\x01" * n).decode(),
                                                        "event_id": 1}})


def test_metadata_and_ping():
    s = CallScript(clips=[])
    s.handle(json.dumps({"type": "conversation_initiation_metadata",
                         "conversation_initiation_metadata_event": {"conversation_id": "conv_1"}}), 0)
    assert s.conversation_id == "conv_1"
    pong = s.handle(json.dumps({"type": "ping", "ping_event": {"event_id": 7, "ping_ms": 50}}), 0)
    assert json.loads(pong) == {"type": "pong", "event_id": 7}


def test_waits_for_agent_then_plays_clip():
    clip = b"\x02" * (CHUNK_BYTES * 2)
    s = CallScript(clips=[clip], agent_quiet_secs=1.0)  # 50 ms of agent audio plays in 0.05 s
    assert s.next_chunk(0.0) == SILENCE  # agent hasn't spoken yet: keep listening
    s.handle(audio(), 0.5)
    assert s.next_chunk(1.0) == SILENCE  # agent still talking
    first = s.next_chunk(2.0)            # quiet for 1.5 s: caller speaks
    assert first == b"\x02" * CHUNK_BYTES
    assert s.next_chunk(2.05) == b"\x02" * CHUNK_BYTES
    assert s.next_chunk(2.1) == SILENCE  # clip done, wait for the agent's answer
    assert not s.done
    s.handle(audio(), 2.5)
    s.next_chunk(5.0)
    assert s.done and s.end_reason == "script finished"


def test_partial_last_chunk_padded():
    s = CallScript(clips=[b"\x03" * 10], agent_quiet_secs=0.1)
    s.handle(audio(), 0)
    chunk = s.next_chunk(1)
    assert len(chunk) == CHUNK_BYTES and chunk.startswith(b"\x03" * 10)


def test_max_secs_cap():
    s = CallScript(clips=[b"x"], max_secs=10)
    s.next_chunk(11)
    assert s.done and "cap" in s.end_reason


def test_transcript_and_tools_recorded():
    s = CallScript(clips=[])
    s.handle(json.dumps({"type": "agent_response", "agent_response_event": {"agent_response": "Hi, this is Cimo"}}), 1)
    s.handle(json.dumps({"type": "user_transcript", "user_transcription_event": {"user_transcript": "Parking?"}}), 2)
    s.handle(json.dumps({"type": "agent_tool_response", "agent_tool_response": {"tool_name": "handover"}}), 3)
    assert [(t.role, t.text) for t in s.transcript] == [("agent", "Hi, this is Cimo"), ("user", "Parking?")]
    assert s.tool_events[0]["type"] == "agent_tool_response"


def test_agent_audio_collected():
    s = CallScript(clips=[])
    s.handle(audio(100), 0)
    s.handle(audio(60), 0.1)
    assert len(s.agent_pcm) == 160


def test_chunk_message_format():
    msg = json.loads(CallScript.chunk_message(b"\x00\x01"))
    assert base64.b64decode(msg["user_audio_chunk"]) == b"\x00\x01"


def test_waits_for_playback_not_arrival():
    """Live finding: 3 s of agent audio can arrive in 0.2 s. The caller must wait until it has PLAYED."""
    s = CallScript(clips=[b"\x05" * CHUNK_BYTES], agent_quiet_secs=0.5)
    three_secs = 16000 * 2 * 3
    s.handle(audio(three_secs // 2), 0.0)
    s.handle(audio(three_secs // 2), 0.2)
    assert s.next_chunk(1.0) == SILENCE   # arrived long ago, still playing
    assert s.next_chunk(3.2) == SILENCE   # finished playing at 3.0, quiet margin not over
    assert s.next_chunk(3.6) == b"\x05" * CHUNK_BYTES
