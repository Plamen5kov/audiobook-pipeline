"""Scoring a transcription against the text that was meant to be spoken.

Pure comparison, no model. The Whisper that produces the transcription lives in
the qa-verifier service; what counts as a match is a judgement about this
corpus and belongs with the rest of the domain logic.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

_NUM_WORDS = {
    "0": "zero", "1": "one", "2": "two", "3": "three", "4": "four",
    "5": "five", "6": "six", "7": "seven", "8": "eight", "9": "nine",
}


def normalise(text: str) -> list[str]:
    """Reduce text to comparable tokens.

    ASR and TTS disagree on surface forms that are not real errors: digits
    versus words, curly versus straight quotes, hyphenation. Normalising these
    away keeps the score measuring content rather than orthography.
    """
    text = text.lower()
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = re.sub(r"[-–—]", " ", text)
    # Spell out standalone digits so "3" matches "three".
    text = re.sub(r"\b\d\b", lambda m: _NUM_WORDS[m.group()], text)
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    return text.split()


def similarity(expected: str, heard: str) -> float:
    """Score how well the transcription matches the source text.

    Uses the better of a word-level and a character-level ratio. Word-level
    alone punishes things that are ASR artefacts rather than synthesis errors:
    a proper noun split across tokens ("Bingley" heard as "Bing Li") scores 0,
    and British spellings transcribed American ("neighbourhood" ->
    "neighborhood") score 0 for that word. Character-level absorbs both while
    still collapsing for the failures we care about, since a dropped or
    hallucinated passage differs at character level too.
    """
    a, b = normalise(expected), normalise(heard)
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    word_ratio = SequenceMatcher(None, a, b).ratio()
    char_ratio = SequenceMatcher(None, "".join(a), "".join(b)).ratio()
    return max(word_ratio, char_ratio)
