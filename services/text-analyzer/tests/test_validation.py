"""Tests for the validation node (Node 6)."""

from app.models import Segment
from app.nodes.validation import validate_completeness


def _seg(id: int, text: str) -> Segment:
    return Segment(id=id, kind="narration", original_text=text, speaker="narrator")


def test_perfect_match():
    text = "Hello world. How are you?"
    segs = [_seg(1, "Hello world. How are you?")]
    passed, issues = validate_completeness(segs, text)
    assert passed is True
    assert issues == []


def test_missing_words():
    text = "Hello world. How are you?"
    segs = [_seg(1, "Hello world.")]
    passed, issues = validate_completeness(segs, text)
    assert passed is False
    assert len(issues) > 0


def test_empty_segments():
    passed, issues = validate_completeness([], "Some text")
    assert passed is False
    assert any("No segments" in i for i in issues)


def test_multi_segment_coverage():
    text = '\u201cHello\u201d she said'
    segs = [
        Segment(id=1, kind="dialogue", original_text="Hello", speaker="unknown"),
        Segment(id=2, kind="narration", original_text="she said", speaker="narrator"),
    ]
    passed, issues = validate_completeness(segs, text)
    assert passed is True
