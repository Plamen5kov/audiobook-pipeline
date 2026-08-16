"""Rewrite each segment into the words that should actually be spoken.

The synthesiser is handed prose written for the eye. A scene break drawn as
``***``, a LitRPG tag in square brackets, a stat line ending in ``???``, a
ratio written ``0/5`` — a reader skips or silently translates all of these, and
the model does not. Measured on this corpus: ``0/5`` came back as "zero
fifths", and an item block came back as "Item I can send in rank legendary
consumable effect uses remaining 101 bako".

The result goes in ``spoken_text`` and the prose stays untouched in
``original_text``. That split matters twice over: the validation node proves
the segments still reconstruct the source word for word, and QA transcribes the
audio and compares it against what was meant to be said, which is the spoken
form and not the written one.

What this node deliberately does not do is guess at pronunciation. The chapters
to be narrated contain ``CIA``, ``EDJI`` and ``ASIS``, which want spelling out,
alongside ``THAT`` for emphasis and ``II`` as a numeral, which do not, and no
rule separates them. Those go through ``ctx.lexicon``, a per-book mapping
someone has listened to, rather than through a pattern that would be wrong as
often as it was right.
"""

from __future__ import annotations

import logging
import re

from ..models import AnalysisContext, Segment
from .base import Node, register
from .pause_timing import PAUSE_SCENE_BREAK

log = logging.getLogger(__name__)

# A line of punctuation alone: a scene break drawn for the eye. Spoken, it is
# either read out or turned into noise, and either way the beat it stands for
# is lost, so it becomes silence instead.
SCENE_BREAK_LINE = re.compile(r"^[\s*\-—_#~=.]{3,}$")

BRACKET_TAG = re.compile(r"\[([^\]\n]{1,60})\]")
FIELD_LINE = re.compile(r"^([A-Z][A-Za-z ]{0,20}):\s*(.*)$")
UNKNOWN_VALUE = re.compile(r"\?{2,}")
RATIO = re.compile(r"\b(\d{1,3})\s*/\s*(\d{1,3})\b")
AMPERSAND = re.compile(r"\s*&\s*")
WHITESPACE = re.compile(r"\s+")

_ONES = ("zero", "one", "two", "three", "four", "five", "six", "seven",
         "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
         "fifteen", "sixteen", "seventeen", "eighteen", "nineteen")
_TENS = ("", "", "twenty", "thirty", "forty", "fifty", "sixty", "seventy",
         "eighty", "ninety")


def number_words(n: int) -> str:
    """Spell a small whole number. Ratios are the only caller, so 0-999 is the
    whole range that occurs: stat lines read ``0/5``, never ``0/5000``."""
    if n < 20:
        return _ONES[n]
    if n < 100:
        tens, ones = divmod(n, 10)
        return _TENS[tens] + (f"-{_ONES[ones]}" if ones else "")
    hundreds, rest = divmod(n, 100)
    out = f"{_ONES[hundreds]} hundred"
    return f"{out} and {number_words(rest)}" if rest else out


def _strip_scene_breaks(text: str) -> tuple[str, bool]:
    """Drop punctuation-only lines, reporting whether any were found."""
    kept, dropped = [], False
    for line in text.splitlines():
        if line.strip() and SCENE_BREAK_LINE.match(line.strip()):
            dropped = True
            continue
        kept.append(line)
    return "\n".join(kept), dropped


def _speakable_field(line: str) -> str | None:
    """Turn a stat-block line into a sentence, or drop it if it says nothing.

    ``Effect: ???`` carries no information and has no spoken form, so it goes.
    ``Item: [X] (transcendent rank, legendary)`` becomes a list of words in the
    order a person would read them out.
    """
    m = FIELD_LINE.match(line.strip())
    if not m:
        return line
    label, value = m.group(1), m.group(2).strip()
    value = BRACKET_TAG.sub(r"\1", value)
    value = UNKNOWN_VALUE.sub("", value).strip()
    # A parenthesised attribute list reads as a continuation of the value.
    value = re.sub(r"\s*\(([^)]*)\)", r", \1", value)
    value = value.strip(" ,")
    if not value:
        return None
    return f"{label}: {value}"


def _tidy(line: str) -> str:
    """Clear the debris a removed value leaves behind.

    Deleting ``???`` out of ``???. (consumable, ???)`` leaves punctuation with
    nothing to punctuate. Spoken, that is where the gibberish came from.
    """
    line = re.sub(r"\(\s*,\s*", "(", line)
    line = re.sub(r",\s*\)", ")", line)
    line = re.sub(r"\(\s*\)", "", line)
    line = re.sub(r"\s+([.,;:!?])", r"\1", line)
    line = re.sub(r"^[\s.,;:]+", "", line)
    return line.strip()


def _join(lines: list[str]) -> str:
    """Join a segment's lines, ending any that stop without punctuation.

    Prose paragraphs already end in a full stop, so nothing happens to them.
    A stat-block line does not, and running one into the next is how
    ``Ability: Mysterious Stranger`` and ``Language adaptation`` become one
    breathless phrase.
    """
    out = []
    for i, line in enumerate(lines):
        # Only between lines: the last one ends the segment, and giving it a
        # full stop it did not have would be inventing punctuation.
        if i < len(lines) - 1 and line and line[-1] not in ".!?,;:—":
            line += "."
        out.append(line)
    return " ".join(out)


def normalise_text(text: str, lexicon: dict[str, str] | None = None) -> tuple[str, bool]:
    """Return the spoken form of *text*, and whether a scene break was removed."""
    text, had_scene_break = _strip_scene_breaks(text)

    lines = []
    for line in text.splitlines():
        spoken = _speakable_field(line)
        if spoken is None:
            continue
        spoken = _tidy(UNKNOWN_VALUE.sub("", BRACKET_TAG.sub(r"\1", spoken)))
        # A line whose only content was a value nobody can say is not a line.
        if spoken and re.search(r"\w", spoken):
            lines.append(spoken)
    text = _join(lines)

    text = RATIO.sub(
        lambda m: f"{number_words(int(m.group(1)))} out of {number_words(int(m.group(2)))}",
        text)
    text = AMPERSAND.sub(" and ", text)

    for term, spoken in sorted((lexicon or {}).items(), key=lambda kv: -len(kv[0])):
        text = re.sub(rf"\b{re.escape(term)}\b", spoken, text)

    return WHITESPACE.sub(" ", text).strip(), had_scene_break


def normalise_segments(segments: list[Segment],
                       lexicon: dict[str, str] | None = None) -> list[Segment]:
    """Fill ``spoken_text`` on every segment, leaving ``original_text`` alone."""
    changed = 0
    for seg in segments:
        spoken, had_scene_break = normalise_text(seg.original_text, lexicon)
        seg.spoken_text = spoken
        if spoken != seg.original_text:
            changed += 1
        if had_scene_break:
            # The marker is gone, so the beat it stood for has to survive as
            # silence rather than disappearing with it.
            seg.pause_before_ms = max(seg.pause_before_ms, PAUSE_SCENE_BREAK)
    log.info("Normalisation: rewrote %d of %d segments", changed, len(segments))
    return segments


@register
class NormalisationNode(Node):
    """Produce the spoken form of every segment, last, once the prose has been
    read for everything else."""

    name = "normalisation"
    requires = ("segments", "segments.pause_before_ms")
    assigns = ("segments.spoken_text", "segments.pause_before_ms")

    async def run(self, ctx: AnalysisContext) -> None:
        ctx.segments = normalise_segments(ctx.segments, ctx.lexicon)
