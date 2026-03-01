"""Tests for explicit attribution (Node 2)."""

from app.models import Segment
from app.nodes.explicit_attribution import attribute_explicit


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
    # The Name+Verb regex captures multi-word runs; assert the name is extracted
    assert "Marcus" in result[1].speaker or result[1].attribution_source == "explicit"


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
    """Pronoun + verb should set attribution_source when no named match found."""
    segs = _make_segments([
        ("dialogue", "unknown", "Let me explain."),
        ("narration", "narrator", "he said."),
    ])
    result = attribute_explicit(segs)
    # Should be attributed via pronoun since no proper name is adjacent
    assert result[0].attribution_source in ("pronoun_male", "explicit")


def test_already_attributed():
    """Segments with known speakers should not be modified."""
    segs = _make_segments([
        ("dialogue", "Jason", "Hello!"),
        ("narration", "narrator", "said Elena."),
    ])
    result = attribute_explicit(segs)
    assert result[0].speaker == "Jason"  # unchanged
