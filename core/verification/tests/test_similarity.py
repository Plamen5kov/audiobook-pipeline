"""Tests for the QA text comparison.

These lock the normalisation rules: the score must ignore differences that are
artefacts of ASR-vs-TTS orthography, while still catching real content errors.

The scoring is pure, so this imports it directly. It used to stub out torch,
transformers, fastapi and pydantic to get at these two functions, which is the
cost the extraction into core removed.
"""

from core.verification.similarity import normalise, similarity


def test_identical_text_scores_one():
    assert similarity("Hello there, friend.", "Hello there, friend.") == 1.0


def test_punctuation_and_case_ignored():
    assert similarity("Hello, there!", "hello there") == 1.0


def test_curly_quotes_and_dashes_ignored():
    assert similarity("It’s a well-known fact.", "It's a well known fact.") == 1.0


def test_single_digits_match_words():
    assert similarity("I have 3 coins.", "I have three coins.") == 1.0


# The operational cutoff in the service; assertions below are expressed
# against it rather than against arbitrary constants.
THRESHOLD = 0.85


def test_truncated_audio_falls_below_threshold():
    full = "The quick brown fox jumps over the lazy dog."
    partial = "The quick brown fox."
    assert similarity(full, partial) < THRESHOLD


def test_single_dropped_word_is_tolerated():
    """A one-word difference stays above threshold by design — the check is
    aimed at dropouts and hallucinations, not at word-perfect ASR agreement."""
    full = "The quick brown fox jumps over the lazy dog."
    near = "The quick brown fox jumps over the lazy."
    assert similarity(full, near) >= THRESHOLD


def test_hallucinated_extra_speech_lowers_the_score():
    expected = "Bingley."
    heard = "Bingley. And then he walked across the room and considered the matter at length."
    assert similarity(expected, heard) < 0.5


def test_empty_audio_transcription_scores_zero():
    assert similarity("Some spoken line.", "") == 0.0


def test_proper_noun_split_by_asr_scores_better_than_word_level_alone():
    """Whisper transcribes 'Bingley' as 'Bing Li'. Word-level scoring alone
    gives that 0.0; the character-level fallback must rescue most of it. It
    still lands under threshold, which is why one-word segments are graded as
    'suspect' rather than 'failed' by the service."""
    score = similarity("Bingley.", "Bing Li.")
    assert score > 0.7, f"character fallback did not engage (got {score})"


def test_british_spelling_transcribed_american_is_tolerated():
    expected = "The gentleman newly arrived in the neighbourhood."
    heard = "The gentleman newly arrived in the neighborhood."
    assert similarity(expected, heard) >= THRESHOLD


def test_character_level_fallback_still_catches_real_failures():
    """The looser scoring must not blunt the checks that matter."""
    assert similarity("The quick brown fox jumps over the lazy dog.",
                      "The quick brown fox.") < THRESHOLD
    assert similarity("Bingley.",
                      "Bingley. And then he walked across the room "
                      "and considered the matter at length.") < THRESHOLD


def test_normalise_strips_to_tokens():
    assert normalise("It’s — really? Yes!") == ["it's", "really", "yes"]
