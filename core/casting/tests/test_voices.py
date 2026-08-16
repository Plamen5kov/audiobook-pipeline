"""Tests for gender-aware voice casting.

The rules under test came from listening to a generated chapter: a male
character was voiced by a woman, and the same character changed voice partway
through.
"""

from core.casting.voices import (
    QWEN_FEMALE,
    QWEN_MALE,
    NARRATOR_VOICE,
    build_voice_mapping,
)


def test_male_character_never_gets_a_female_voice():
    chars = [{"name": "Jason", "gender": "male"}]
    m = build_voice_mapping(chars, ["Jason"])
    assert m["Jason"] in QWEN_MALE
    assert m["Jason"] not in QWEN_FEMALE


def test_female_character_never_gets_a_male_voice():
    chars = [{"name": "Elena", "gender": "female"}]
    m = build_voice_mapping(chars, ["Elena"])
    assert m["Elena"] in QWEN_FEMALE
    assert m["Elena"] not in QWEN_MALE


def test_assignment_is_stable_across_calls():
    """Same cast in, same voices out — regardless of the order given."""
    chars = [
        {"name": "Jason", "gender": "male"},
        {"name": "Elena", "gender": "female"},
        {"name": "Marcus", "gender": "male"},
    ]
    a = build_voice_mapping(chars, ["Jason", "Elena", "Marcus", "narrator"])
    b = build_voice_mapping(list(reversed(chars)), ["narrator", "Marcus", "Elena", "Jason"])
    assert a == b


def test_distinct_characters_of_same_gender_get_distinct_voices():
    chars = [
        {"name": "Jason", "gender": "male"},
        {"name": "Marcus", "gender": "male"},
        {"name": "Rufus", "gender": "male"},
    ]
    m = build_voice_mapping(chars, ["Jason", "Marcus", "Rufus"])
    assert len({m["Jason"], m["Marcus"], m["Rufus"]}) == 3


def test_explicit_mapping_is_never_overridden():
    chars = [{"name": "Jason", "gender": "male"}]
    m = build_voice_mapping(chars, ["Jason"], existing={"Jason": "Sohee"})
    assert m["Jason"] == "Sohee"


def test_narrator_gets_the_fixed_narrator_voice():
    chars = [{"name": "Jason", "gender": "male"}]
    m = build_voice_mapping(chars, ["Jason", "narrator"])
    assert m["narrator"] == NARRATOR_VOICE


def test_narrator_voice_is_not_reused_for_a_character():
    """Regression: the narrator voice is also first in the male pool, so a
    male character sorted before 'narrator' took it and the protagonist ended
    up sounding identical to the narrator."""
    chars = [{"name": "Jason", "gender": "male"}]
    m = build_voice_mapping(chars, ["Jason", "narrator", "unknown"])
    assert m["narrator"] == NARRATOR_VOICE
    assert m["Jason"] != m["narrator"]
    assert len(set(m.values())) == len(m), f"voices collide: {m}"


def test_no_two_speakers_share_a_voice_in_a_typical_cast():
    chars = [
        {"name": "Jason", "gender": "male"},
        {"name": "Farrah", "gender": "female"},
        {"name": "Rufus", "gender": "male"},
    ]
    speakers = ["Jason", "Farrah", "Rufus", "narrator", "unknown"]
    m = build_voice_mapping(chars, speakers)
    assert len(set(m.values())) == len(speakers), f"voices collide: {m}"


def test_unknown_gender_still_gets_a_voice():
    m = build_voice_mapping([], ["unknown"])
    assert m["unknown"]


def test_every_speaker_is_covered():
    chars = [{"name": "Jason", "gender": "male"}]
    speakers = ["Jason", "narrator", "unknown", "Someone"]
    m = build_voice_mapping(chars, speakers)
    assert set(m) == set(speakers)
    assert all(v for v in m.values())


def test_exhausted_pool_reuses_within_gender_rather_than_crossing():
    """More male characters than male voices must still never yield a female
    voice — a repeated male voice is far less wrong than a wrong-gender one."""
    n = len(QWEN_MALE) + 2
    chars = [{"name": f"M{i}", "gender": "male"} for i in range(n)]
    m = build_voice_mapping(chars, [c["name"] for c in chars])
    assert all(v in QWEN_MALE for v in m.values())
