"""Tests for turn-taking and sole-character resolution.

Motivated by a real chapter where 35 of 98 segments stayed 'unknown' and were
cast to a separate voice, so the protagonist audibly changed voice mid-chapter.
"""

from core.analysis.models import Segment
from core.analysis.nodes.turn_taking import apply_turn_taking


def _segs(specs):
    """specs: list of (kind, speaker, attribution_source, text)."""
    return [
        Segment(id=i + 1, kind=k, original_text=t, speaker=sp, attribution_source=src)
        for i, (k, sp, src, t) in enumerate(specs)
    ]


def test_two_speakers_alternate():
    segs = _segs([
        ("dialogue", "Jason", "explicit", "First line."),
        ("dialogue", "Rufus", "explicit", "Second line."),
        ("dialogue", "unknown", "none", "Third line."),
    ])
    apply_turn_taking(segs)
    assert segs[2].speaker == "Jason"


def test_sole_speaker_in_block_claims_unattributed_dialogue():
    """Regression: with only one established speaker there is nobody to
    alternate with, so lines used to stay unknown and get their own voice."""
    segs = _segs([
        ("dialogue", "Jason", "explicit", "Where am I?"),
        ("dialogue", "unknown", "none", "This has to be a dream."),
        ("dialogue", "unknown", "none", "Hello?"),
    ])
    apply_turn_taking(segs)
    assert segs[1].speaker == "Jason"
    assert segs[2].speaker == "Jason"


def test_sole_character_across_whole_chapter():
    """Narration resets conversation blocks, so leftovers must still resolve
    when the chapter has exactly one identified character."""
    segs = _segs([
        ("dialogue", "Jason", "explicit", "Where am I?"),
        ("narration", "narrator", "none", "He looked around."),
        ("narration", "narrator", "none", "Nothing answered."),
        ("narration", "narrator", "none", "The sky was wrong."),
        ("dialogue", "unknown", "none", "Seriously, where am I?"),
    ])
    apply_turn_taking(segs)
    assert segs[4].speaker == "Jason"
    assert segs[4].attribution_source == "sole_character"


def test_sole_character_does_not_fire_with_two_characters():
    """With a real second character, guessing would be wrong; leave it to the
    alternation logic and the AI node instead."""
    segs = _segs([
        ("dialogue", "Jason", "explicit", "One."),
        ("narration", "narrator", "none", "A pause."),
        ("narration", "narrator", "none", "Another pause."),
        ("narration", "narrator", "none", "And another."),
        ("dialogue", "Farrah", "explicit", "Two."),
        ("narration", "narrator", "none", "Silence."),
        ("narration", "narrator", "none", "More silence."),
        ("narration", "narrator", "none", "Still silence."),
        ("dialogue", "unknown", "none", "Three."),
    ])
    apply_turn_taking(segs)
    assert segs[8].speaker == "unknown"


def test_narrator_is_never_reassigned():
    segs = _segs([
        ("dialogue", "Jason", "explicit", "A line."),
        ("narration", "narrator", "none", "Some narration."),
    ])
    apply_turn_taking(segs)
    assert segs[1].speaker == "narrator"
