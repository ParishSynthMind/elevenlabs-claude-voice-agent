import json
from pathlib import Path

import httpx
import pytest

from voiceagent import narration, pronunciation
from voiceagent.config import Settings
from voiceagent.elevenlabs_api import ElevenLabsClient

from .test_api import Fake

RULES = Path(__file__).resolve().parent.parent / "data" / "pronunciation.json"


def test_shipped_dictionary_is_valid():
    data = pronunciation.load(RULES)
    assert len(data["rules"]) >= 3 and all(r["type"] == "alias" for r in data["rules"])


@pytest.mark.parametrize("rules,msg", [
    ([], "at least one"),
    ([{"string_to_replace": "", "type": "alias", "alias": "x"}], "non-empty"),
    ([{"string_to_replace": "a", "type": "alias", "alias": ""}], "no alias"),
    ([{"string_to_replace": "a", "type": "phoneme", "phoneme": "", "alphabet": "ipa"}], "no phoneme"),
    ([{"string_to_replace": "a", "type": "phoneme", "phoneme": "x", "alphabet": "klingon"}], "alphabet"),
    ([{"string_to_replace": "a", "type": "sound", "alias": "x"}], "type must be"),
    ([{"string_to_replace": "a", "type": "alias", "alias": "x", "colour": 1}], "unknown field"),
    ([{"string_to_replace": "Cimo", "type": "alias", "alias": "x"},
      {"string_to_replace": "Cimo", "type": "alias", "alias": "y"}], "twice"),
])
def test_invalid_rules(rules, msg):
    with pytest.raises(pronunciation.DictionaryError, match=msg):
        pronunciation.validate(rules)


def test_duplicate_case_insensitive():
    with pytest.raises(pronunciation.DictionaryError):
        pronunciation.validate([{"string_to_replace": "Quay", "type": "alias", "alias": "key", "case_sensitive": False},
                                {"string_to_replace": "quay", "type": "alias", "alias": "kay", "case_sensitive": False}])


def test_alias_preview():
    rules = pronunciation.load(RULES)["rules"]
    out = pronunciation.apply_aliases("Cimo at HDC on the Quay. Ask a Periodontist about Invisalign.", rules)
    assert "Chee-mo" in out and "Harbour Dental Clinic" in out and "key" in out
    assert "perry-oh-don-tist" in out and "in-vizz-a-line" in out


def test_alias_respects_word_boundaries():
    rules = [{"string_to_replace": "HDC", "type": "alias", "alias": "Harbour"}]
    assert pronunciation.apply_aliases("HDCX HDC", rules) == "HDCX Harbour"


def test_pls_export():
    pls = pronunciation.to_pls([{"string_to_replace": "A&B", "type": "alias", "alias": "A and B"},
                                {"string_to_replace": "tomato", "type": "phoneme", "phoneme": "təˈmɑːtoʊ", "alphabet": "ipa"}])
    assert "<grapheme>A&amp;B</grapheme>" in pls and "<alias>A and B</alias>" in pls and "<phoneme>təˈmɑːtoʊ</phoneme>" in pls
    assert pls.startswith('<?xml version="1.0"')


def test_phoneme_rules_flagged():
    w = pronunciation.phoneme_warnings([{"string_to_replace": "x", "type": "phoneme", "phoneme": "y", "alphabet": "ipa"},
                                        {"string_to_replace": "z", "type": "alias", "alias": "q"}], "eleven_flash_v2_5")
    assert [x[0] for x in w] == ["x"]


def test_clean_script():
    assert narration.clean_script("# title\nHello   there.\n\n  Second line.\r\n") == "Hello there. Second line."


def test_estimate():
    assert narration.estimate_credits(300, "eleven_flash_v2_5") == 150
    assert narration.estimate_credits(301, "eleven_flash_v2_5") == 151
    assert narration.estimate_credits(300, "eleven_multilingual_v2") == 300


def test_plan_refuses_long_scripts():
    with pytest.raises(narration.NarrationError, match="over the 50-character cap"):
        narration.plan("word " * 40, "eleven_flash_v2_5", "v", 50)


def test_plan_refuses_empty():
    with pytest.raises(narration.NarrationError):
        narration.plan("# only a comment\n", "eleven_flash_v2_5", "v", 50)


def test_default_cap_is_small():
    assert Settings().narration_max_chars <= 600


def test_narrate_writes_audio_and_ai_label(tmp_path):
    fake = Fake()
    counts = iter([100, 160])

    def handler(request):
        if request.url.path == "/v1/user/subscription":
            return httpx.Response(200, json={"character_count": next(counts)})
        return fake(request)
    client = ElevenLabsClient("k", transport=httpx.MockTransport(handler))
    p = narration.plan("Welcome to HDC.", "eleven_flash_v2_5", "voice1", 600,
                       {"pronunciation_dictionary_id": "d", "version_id": "v"})
    meta = narration.narrate(client, p, tmp_path, "sample")
    assert (tmp_path / "sample.mp3").read_bytes() == b"ID3fakeaudio"
    side = json.loads((tmp_path / "sample.json").read_text())
    assert side["ai_generated"] is True and "AI-generated" in side["label"]
    assert side["credits_used_measured"] == 60 and side["request_id"] == "req_1"
    assert side["dictionary"]["pronunciation_dictionary_id"] == "d" and meta["characters"] == len("Welcome to HDC.")
