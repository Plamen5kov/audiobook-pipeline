"""Node 5 — Pause/Timing.

Assigns ``pause_before_ms`` to each segment based on structural cues
(paragraph breaks, dialogue turns, scene breaks).
"""

from __future__ import annotations

import logging
from ..models import Segment
from ..timing import timed_node

log = logging.getLogger(__name__)

# Pause durations in milliseconds.
PAUSE_FIRST = 0
PAUSE_SCENE_BREAK = 1000          # blank line between paragraphs
PAUSE_PARAGRAPH_BREAK = 500       # new paragraph (narrator → narrator)
PAUSE_DIALOGUE_AFTER_NARRATION = 350
PAUSE_NARRATION_AFTER_DIALOGUE = 300
PAUSE_DIALOGUE_TURN = 250         # consecutive dialogue in same paragraph


@timed_node("pause_timing", "programmatic")
def assign_pauses(segments: list[Segment]) -> list[Segment]:
    """Set ``pause_before_ms`` on every segment based on its structural
    relationship to the previous segment.

    Modifies segments in place and returns the same list.
    """
    for i, seg in enumerate(segments):
        if i == 0:
            seg.pause_before_ms = PAUSE_FIRST
            continue

        prev = segments[i - 1]

        # Paragraph gap (difference in paragraph_index).
        para_gap = seg.paragraph_index - prev.paragraph_index

        if para_gap > 1 and not (prev.kind == "narration" and seg.kind == "narration"):
            # Skipped paragraph(s) with a kind transition → scene break.
            # Consecutive narration uses paragraph break even with a gap
            # (the gap is an artifact of narration-merging in the splitter).
            seg.pause_before_ms = PAUSE_SCENE_BREAK
        elif para_gap >= 1:
            # Adjacent paragraphs — new paragraph.
            seg.pause_before_ms = PAUSE_PARAGRAPH_BREAK
        elif seg.kind == "dialogue" and prev.kind == "narration":
            seg.pause_before_ms = PAUSE_DIALOGUE_AFTER_NARRATION
        elif seg.kind == "narration" and prev.kind == "dialogue":
            seg.pause_before_ms = PAUSE_NARRATION_AFTER_DIALOGUE
        elif seg.kind == "dialogue" and prev.kind == "dialogue":
            seg.pause_before_ms = PAUSE_DIALOGUE_TURN
        else:
            seg.pause_before_ms = PAUSE_PARAGRAPH_BREAK

    return segments
