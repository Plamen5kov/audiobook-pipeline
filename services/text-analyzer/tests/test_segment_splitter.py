"""Tests for the segment splitter (Node 1)."""

from app.nodes.segment_splitter import split_segments


def test_simple_dialogue_and_narration():
    text = '\u201cHello!\u201d she said.\nIt was a fine day.'
    segments = split_segments(text)

    assert len(segments) >= 2
    dialogue = [s for s in segments if s.kind == "dialogue"]
    narration = [s for s in segments if s.kind == "narration"]
    assert len(dialogue) >= 1
    assert len(narration) >= 1
    assert dialogue[0].original_text == "Hello!"


def test_multiple_dialogue_lines():
    text = '\u201cFirst line,\u201d he said. \u201cSecond line.\u201d'
    segments = split_segments(text)

    dialogue = [s for s in segments if s.kind == "dialogue"]
    assert len(dialogue) == 2
    assert dialogue[0].original_text == "First line,"
    assert dialogue[1].original_text == "Second line."


def test_straight_quotes():
    text = '"What is this?" Jason asked.'
    segments = split_segments(text)

    dialogue = [s for s in segments if s.kind == "dialogue"]
    assert len(dialogue) >= 1
    assert "What is this?" in dialogue[0].original_text


def test_pure_narration():
    text = "The sun was setting over the mountains. It was peaceful."
    segments = split_segments(text)

    assert all(s.kind == "narration" for s in segments)
    assert len(segments) >= 1


def test_empty_input():
    segments = split_segments("")
    assert segments == []


def test_sequential_ids():
    text = '\u201cHello,\u201d she said. \u201cGoodbye.\u201d He waved.'
    segments = split_segments(text)

    ids = [s.id for s in segments]
    assert ids == list(range(1, len(ids) + 1))


def test_all_text_preserved():
    """Every word from the original should appear in some segment."""
    text = '\u201cWhat the bloody hell is going on?\u201d Jason asked.\nSomething appeared before him.'
    segments = split_segments(text)

    all_words = set()
    for s in segments:
        for w in s.original_text.lower().split():
            all_words.add(w.strip(".,!?"))

    for w in ["what", "bloody", "hell", "going", "jason", "asked", "something", "appeared"]:
        assert w in all_words, f"Missing word: {w}"
