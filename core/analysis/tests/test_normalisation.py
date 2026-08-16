"""Tests for the spoken-form rewrite.

The inputs here are taken from the two corpora rather than invented: the
``***`` scene break that appears 130 times across the chapters to be narrated,
and the bracket tags, ``???`` fields and ``0/5`` ratios from the LitRPG stat
blocks in book one, one of which QA caught being synthesised as gibberish.
"""

import asyncio

from core.analysis.models import AnalysisContext, Segment
from core.analysis.nodes.normalisation import (
    normalise_segments,
    normalise_text,
    number_words,
)
from core.analysis.nodes.pause_timing import PAUSE_SCENE_BREAK
from core.analysis.pipeline import DEFAULT_PIPELINE, Pipeline, run_analysis


def spoken(text, lexicon=None):
    return normalise_text(text, lexicon)[0]


def seg(text, **kw):
    return Segment(id=kw.pop("id", 1), kind=kw.pop("kind", "narration"),
                   original_text=text, **kw)


def test_scene_break_is_not_spoken():
    text = "***\nAs his helicopter descended to the tarmac, Michael put away the papers."
    assert spoken(text) == (
        "As his helicopter descended to the tarmac, Michael put away the papers.")


def test_a_segment_that_is_only_a_scene_break_becomes_empty():
    assert spoken("***") == ""


def test_scene_break_becomes_silence_rather_than_vanishing():
    s = seg("***\nMichael observed through the window.", pause_before_ms=250)
    normalise_segments([s])
    assert s.pause_before_ms == PAUSE_SCENE_BREAK


def test_an_existing_longer_pause_is_kept():
    s = seg("***\nMichael observed.", pause_before_ms=PAUSE_SCENE_BREAK + 500)
    normalise_segments([s])
    assert s.pause_before_ms == PAUSE_SCENE_BREAK + 500


def test_other_rule_shapes_are_scene_breaks_too():
    for rule in ("* * *", "---", "###", "___"):
        assert spoken(f"{rule}\nHe walked in.") == "He walked in."


def test_prose_that_merely_contains_an_asterisk_is_left_alone():
    assert spoken("She marked it with an * in the margin.") == (
        "She marked it with an * in the margin.")


def test_bracket_tags_lose_their_brackets():
    assert spoken("He gained [Astral Affinity] and [No Attribute].") == (
        "He gained Astral Affinity and No Attribute.")


def test_ratios_are_read_as_words():
    assert spoken("Progress: 0/5") == "Progress: zero out of five"
    assert spoken("You have 0/1 essences.") == "You have zero out of one essences."


def test_a_field_that_says_nothing_is_dropped():
    assert spoken("Effect: ???") == ""


def test_item_block_becomes_a_readable_sentence():
    text = "Item: [World-Phoenix Token] (transcendent rank, legendary)"
    assert spoken(text) == "Item: World-Phoenix Token, transcendent rank, legendary"


def test_the_stat_block_qa_caught_as_gibberish():
    """Segment 71 of book one, chapter one, verbatim.

    Synthesised from the written form it came back as "Item I can send in rank
    legendary consumable effect uses remaining 101 bako".
    """
    block = ("Item: [World-Phoenix Token] (transcendent rank, legendary)\n"
             "???. (consumable, ???)\n"
             "Effect: ???\n"
             "Effect: ???\n"
             "Uses remaining: 1/1")
    assert spoken(block) == (
        "Item: World-Phoenix Token, transcendent rank, legendary. "
        "(consumable). Uses remaining: one out of one")


def test_ampersand_is_read_as_and():
    assert spoken("Department of Prime Minister & Cabinet") == (
        "Department of Prime Minister and Cabinet")
    assert spoken("a short Q&A") == "a short Q and A"


def test_lexicon_replaces_terms_no_rule_could_derive():
    lex = {"EDJI": "E D J I", "g'day": "gidday"}
    assert spoken("The EDJI was formed.", lex) == "The E D J I was formed."
    assert spoken("Um, g'day?", lex) == "Um, gidday?"


def test_lexicon_prefers_the_longer_term():
    lex = {"US": "U S", "USA": "U S A"}
    assert spoken("the USA and the US", lex) == "the U S A and the U S"


def test_capitals_are_left_alone_without_a_lexicon():
    # THAT is emphasis and II is a numeral; no rule separates them from CIA,
    # so nothing is guessed.
    assert spoken("I said THAT to the CIA, part II.") == (
        "I said THAT to the CIA, part II.")


def test_whitespace_is_collapsed():
    assert spoken("He stopped.\n\n  Then he ran.") == "He stopped. Then he ran."


def test_number_words_cover_the_range_ratios_use():
    assert number_words(0) == "zero"
    assert number_words(5) == "five"
    assert number_words(19) == "nineteen"
    assert number_words(20) == "twenty"
    assert number_words(42) == "forty-two"
    assert number_words(100) == "one hundred"
    assert number_words(101) == "one hundred and one"


def test_original_text_is_never_touched():
    original = "***\nHe gained [Astral Affinity]. Progress: 0/5"
    s = seg(original)
    normalise_segments([s])
    assert s.original_text == original
    assert s.spoken_text != original


def test_unchanged_prose_still_gets_a_spoken_form():
    s = seg("He walked into the room.")
    normalise_segments([s])
    assert s.spoken_text == "He walked into the room."


def test_node_runs_in_the_pipeline_and_output_carries_spoken_text():
    text = "***\n\nHe gained [Astral Affinity].\n\n“Progress: 0/5,” he said.\n"
    ctx = AnalysisContext(text=text, title="ch1")
    deterministic = [n for n in DEFAULT_PIPELINE
                     if n not in ("ai_attribution", "emotion_classifier")]
    result = asyncio.run(run_analysis(ctx, Pipeline.from_names(deterministic)))

    joined = " ".join(s["spoken_text"] for s in result.segments)
    assert "[" not in joined and "***" not in joined
    assert "zero out of five" in joined
    # The prose the validation node checks is still verbatim.
    assert ctx.validation["passed"], ctx.validation["issues"]


def test_normalisation_runs_after_pause_timing():
    """Its pause bump would be overwritten if pause_timing ran later."""
    order = list(DEFAULT_PIPELINE)
    assert order.index("normalisation") > order.index("pause_timing")
