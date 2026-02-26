"""Node 1 — Segment Splitter.

Character-by-character state machine that splits text at quote boundaries
into dialogue vs narration segments.  Guarantees verbatim text by construction.

Handles:
- Straight quotes ("...") and curly quotes (\u201c...\u201d)
- Apostrophes inside words (don't, it's) — NOT treated as quote boundaries
- Split dialogue: "X," she said. "Y." → 3 segments
- Multi-paragraph dialogue (opening quote without closing = continuation)
"""

from __future__ import annotations

import logging
from ..models import Segment
from ..timing import timed_node

log = logging.getLogger(__name__)

OPEN_QUOTES = {"\u201c", "\u00ab"}  # left double curly, guillemet
CLOSE_QUOTES = {"\u201d", "\u00bb"}
STRAIGHT_DOUBLE = '"'

# Characters that, when appearing before a straight quote,
# indicate it is likely a closing quote rather than an opening one.
_CLOSING_CONTEXT = set("abcdefghijklmnopqrstuvwxyz"
                       "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                       "0123456789"
                       ".,!?;\u2026\u2019'")


@timed_node("segment_splitter", "programmatic")
def split_segments(text: str) -> list[Segment]:
    """Split *text* into an ordered list of Segment objects.

    Every character of *text* is accounted for by exactly one segment's
    ``char_offset_start .. char_offset_end`` span.  Quotation-mark characters
    themselves are excluded from ``original_text`` but included in the span
    so that validation can verify full coverage.
    """
    paragraphs = _split_paragraphs(text)
    segments: list[Segment] = []
    seg_id = 1
    global_offset = 0

    for para_idx, paragraph in enumerate(paragraphs):
        if not paragraph.strip():
            # Empty paragraph (blank line) — record the offset but skip.
            global_offset += len(paragraph) + 1  # +1 for the newline
            continue

        spans = _extract_quote_spans(paragraph)

        for span_start, span_end, kind, raw_text in spans:
            stripped = raw_text.strip()
            if not stripped:
                continue

            segments.append(Segment(
                id=seg_id,
                kind=kind,
                original_text=stripped,
                speaker="narrator" if kind == "narration" else "unknown",
                paragraph_index=para_idx,
                char_offset_start=global_offset + span_start,
                char_offset_end=global_offset + span_end,
            ))
            seg_id += 1

        global_offset += len(paragraph) + 1  # +1 for the newline separator

    # Merge consecutive narration segments from adjacent paragraphs
    # (up to a maximum length) to avoid over-fragmentation.
    segments = _merge_consecutive_narration(segments, max_chars=800)

    log.info("Segment splitter produced %d segments (%d dialogue, %d narration)",
             len(segments),
             sum(1 for s in segments if s.kind == "dialogue"),
             sum(1 for s in segments if s.kind == "narration"))
    return segments


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _split_paragraphs(text: str) -> list[str]:
    """Split on newlines, preserving blanks so paragraph indices stay stable."""
    return text.split("\n")


def _is_apostrophe(text: str, pos: int) -> bool:
    """Return True if the character at *pos* is an apostrophe inside a word."""
    ch = text[pos]
    if ch not in ("'", "\u2019"):
        return False
    # Apostrophe if preceded AND followed by a letter.
    if pos > 0 and pos < len(text) - 1:
        return text[pos - 1].isalpha() and text[pos + 1].isalpha()
    return False


def _is_closing_straight_quote(text: str, pos: int) -> bool:
    """Heuristic: a straight double-quote is *closing* if the preceding
    character is a letter, digit, or sentence-ending punctuation."""
    if pos == 0:
        return False
    return text[pos - 1] in _CLOSING_CONTEXT


def _extract_quote_spans(text: str) -> list[tuple[int, int, str, str]]:
    """Identify dialogue / narration spans within a single paragraph.

    Returns a list of ``(start, end, kind, raw_text)`` tuples where
    *start*/*end* are character offsets into *text*, *kind* is
    ``"dialogue"`` or ``"narration"``, and *raw_text* is the text content
    (without surrounding quote marks for dialogue).
    """
    spans: list[tuple[int, int, str, str]] = []
    state = "narration"
    span_start = 0
    dialogue_text_start = 0  # where the actual dialogue text begins (after open quote)
    i = 0

    while i < len(text):
        ch = text[i]

        if state == "narration":
            if ch in OPEN_QUOTES or (ch == STRAIGHT_DOUBLE and not _is_closing_straight_quote(text, i)):
                # Flush narration span up to (and excluding) the quote char.
                if i > span_start:
                    raw = text[span_start:i]
                    spans.append((span_start, i, "narration", raw))
                # Start dialogue — the text begins after the quote character.
                span_start = i  # span includes the quote char for offset coverage
                dialogue_text_start = i + 1
                state = "dialogue"

        elif state == "dialogue":
            if ch in CLOSE_QUOTES or (ch == STRAIGHT_DOUBLE and _is_closing_straight_quote(text, i)):
                # Flush dialogue span (text excludes surrounding quotes).
                raw = text[dialogue_text_start:i]
                # Span covers from the opening quote through the closing quote.
                spans.append((span_start, i + 1, "dialogue", raw))
                span_start = i + 1
                state = "narration"

        i += 1

    # Flush remaining text.
    if span_start < len(text):
        remaining_raw = text[span_start:]
        if state == "dialogue":
            # Unclosed quote — multi-paragraph dialogue continuation.
            raw = text[dialogue_text_start:]
            spans.append((span_start, len(text), "dialogue", raw))
        else:
            spans.append((span_start, len(text), "narration", remaining_raw))

    return spans


def _merge_consecutive_narration(segments: list[Segment], max_chars: int = 800) -> list[Segment]:
    """Merge consecutive narration segments (from different paragraphs)
    into a single segment, up to *max_chars* total length.

    This prevents over-fragmentation where every paragraph becomes its own
    narration segment.  The merge stops at dialogue boundaries and when
    the accumulated text would exceed *max_chars*.
    """
    if not segments:
        return segments

    merged: list[Segment] = [segments[0]]
    for seg in segments[1:]:
        prev = merged[-1]
        if (prev.kind == "narration"
                and seg.kind == "narration"
                and len(prev.original_text) + len(seg.original_text) + 1 <= max_chars):
            # Merge: extend the previous segment.
            prev.original_text = prev.original_text + "\n" + seg.original_text
            prev.char_offset_end = seg.char_offset_end
            # Keep the earlier paragraph_index.
        else:
            merged.append(seg)

    # Re-number IDs sequentially after merging.
    for i, seg in enumerate(merged, start=1):
        seg.id = i

    return merged
