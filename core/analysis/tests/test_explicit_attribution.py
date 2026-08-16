"""Tests for explicit attribution (Node 2)."""

from core.analysis.models import Segment
from core.analysis.nodes.explicit_attribution import attribute_explicit


def _make_segments(specs: list[tuple[str, str, str]]) -> list[Segment]:
    """Build segments from (kind, speaker, text) tuples."""
    return [
        Segment(id=i + 1, kind=kind, original_text=text, speaker=speaker)
        for i, (kind, speaker, text) in enumerate(specs)
    ]


def test_verb_name_pattern():
    """Pattern: 'said Elena' in adjacent narration should extract name."""
    segs = _make_segments([
        ("dialogue", "unknown", "Hello there!"),
        ("narration", "narrator", "said Elena."),
    ])
    result = attribute_explicit(segs)
    assert result[0].speaker == "Elena"
    assert result[0].attribution_source == "explicit"


def test_name_verb_pattern():
    """Pattern: 'Marcus said' in adjacent narration."""
    segs = _make_segments([
        ("narration", "narrator", "Marcus replied."),
        ("dialogue", "unknown", "Be quiet!"),
    ])
    result = attribute_explicit(segs)
    assert result[1].speaker == "Marcus"
    assert result[1].attribution_source == "explicit"


def test_no_adjacent_narration():
    """Dialogue without adjacent narration should stay unknown."""
    segs = _make_segments([
        ("dialogue", "unknown", "Hello!"),
        ("dialogue", "unknown", "Hi!"),
    ])
    result = attribute_explicit(segs)
    assert result[0].speaker == "unknown"
    assert result[1].speaker == "unknown"


def test_pronoun_attribution():
    """Pronoun + verb must take the pronoun path, never become a speaker."""
    segs = _make_segments([
        ("dialogue", "unknown", "Let me explain."),
        ("narration", "narrator", "he said."),
    ])
    result = attribute_explicit(segs)
    assert result[0].attribution_source == "pronoun_male"
    assert result[0].speaker == "unknown"


def test_lowercase_words_never_become_speakers():
    """Regression: re.IGNORECASE let [A-Z] match lowercase, so narration like
    'he said to his companion' produced the speaker 'to his companion'."""
    cases = [
        "he said to his companion.",
        "she asked against her better judgement.",
        "replied he, and turned away.",
        "said she, with feeling.",
    ]
    for narration in cases:
        segs = _make_segments([
            ("dialogue", "unknown", "Some line of dialogue."),
            ("narration", "narrator", narration),
        ])
        result = attribute_explicit(segs)
        assert result[0].speaker == "unknown", (
            f"{narration!r} produced speaker {result[0].speaker!r}"
        )


def test_sentence_initial_common_words_never_become_speakers():
    """Regression: capitalisation carries no information at the start of a
    sentence, so 'He said.' and 'Neither replied.' produced the characters
    'He' and 'Neither' in a real chapter run."""
    for narration in [
        "He said, turning away.",
        "Neither replied for a long moment.",
        "She asked, quietly.",
        "Instead he muttered something.",
        "Eventually someone answered.",
    ]:
        segs = _make_segments([
            ("dialogue", "unknown", "A line of dialogue."),
            ("narration", "narrator", narration),
        ])
        result = attribute_explicit(segs)
        assert result[0].speaker == "unknown", (
            f"{narration!r} produced speaker {result[0].speaker!r}"
        )


def test_real_name_still_extracted_around_pronouns():
    """A genuine name must still win even when pronouns are nearby."""
    segs = _make_segments([
        ("dialogue", "unknown", "You are mistaken."),
        ("narration", "narrator", "said Elizabeth, before he could answer her."),
    ])
    result = attribute_explicit(segs)
    assert result[0].speaker == "Elizabeth"
    assert result[0].attribution_source == "explicit"


def test_already_attributed():
    """Segments with known speakers should not be modified."""
    segs = _make_segments([
        ("dialogue", "Jason", "Hello!"),
        ("narration", "narrator", "said Elena."),
    ])
    result = attribute_explicit(segs)
    assert result[0].speaker == "Jason"  # unchanged
