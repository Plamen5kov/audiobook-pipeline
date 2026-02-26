"""Node 3 — Turn-Taking Heuristic.

In multi-character conversations where explicit attributions drop after
the first few exchanges, speakers typically alternate.  This node resolves
``speaker="unknown"`` dialogue segments by alternating between the two
most recently established speakers.

A "conversation block" resets when there are two or more consecutive
narration-only segments (indicating a scene or topic change).
"""

from __future__ import annotations

import logging
from ..models import Segment

log = logging.getLogger(__name__)

# Maximum gap of consecutive narration segments before we reset the
# conversation context.
_NARRATION_GAP_RESET = 2


def apply_turn_taking(segments: list[Segment]) -> list[Segment]:
    """Resolve remaining ``speaker="unknown"`` dialogue segments using
    turn-taking alternation.

    Also resolves pronoun-tagged segments ("pronoun_male" / "pronoun_female")
    when there is exactly one character of that gender in the conversation.

    Modifies segments in place and returns the same list.
    """
    # First pass: resolve pronoun attributions where gender is unambiguous.
    _resolve_pronouns(segments)

    # Second pass: alternate speakers in conversation blocks.
    _alternate_speakers(segments)

    return segments


def _resolve_pronouns(segments: list[Segment]) -> None:
    """If a segment has attribution_source='pronoun_male' or 'pronoun_female',
    and there is exactly one known character of that gender nearby, assign it.
    """
    # Collect known speakers and their genders from explicit attributions.
    known_speakers: dict[str, str | None] = {}  # name → gender or None
    for seg in segments:
        if seg.attribution_source == "explicit" and seg.speaker != "narrator":
            known_speakers.setdefault(seg.speaker, None)

    # Infer gender from pronoun context in nearby segments.
    for i, seg in enumerate(segments):
        if seg.attribution_source == "explicit" and seg.speaker in known_speakers:
            # Check adjacent narration for gender pronouns.
            for j in (i - 1, i + 1):
                if 0 <= j < len(segments) and segments[j].kind == "narration":
                    text_lower = segments[j].original_text.lower()
                    if " he " in text_lower or " his " in text_lower or " him " in text_lower:
                        known_speakers[seg.speaker] = "male"
                    elif " she " in text_lower or " her " in text_lower or " hers " in text_lower:
                        known_speakers[seg.speaker] = "female"

    # Now resolve pronoun segments.
    for seg in segments:
        if seg.attribution_source.startswith("pronoun_"):
            gender = seg.attribution_source.split("_", 1)[1]
            candidates = [name for name, g in known_speakers.items() if g == gender]
            if len(candidates) == 1:
                seg.speaker = candidates[0]
                seg.attribution_source = "turn_taking"
                log.debug("Pronoun resolved: segment %d → %s (only %s)",
                          seg.id, candidates[0], gender)


def _alternate_speakers(segments: list[Segment]) -> None:
    """Walk through segments and alternate unknown speakers between the
    two most recent distinct speakers in each conversation block."""
    conv_speakers: list[str] = []  # ordered history within current block
    narration_streak = 0

    for seg in segments:
        # Track narration streaks to detect block boundaries.
        if seg.kind == "narration":
            narration_streak += 1
            if narration_streak >= _NARRATION_GAP_RESET:
                conv_speakers.clear()
            continue

        narration_streak = 0

        if seg.kind == "dialogue":
            if seg.speaker not in ("unknown",) and seg.attribution_source != "none":
                # Known speaker — add to conversation history.
                if not conv_speakers or conv_speakers[-1] != seg.speaker:
                    conv_speakers.append(seg.speaker)
                continue

            # Unknown speaker — try to alternate.
            if len(conv_speakers) >= 2:
                last = conv_speakers[-1]
                second_last = conv_speakers[-2]
                # Alternate: assign the other speaker.
                assigned = second_last if last == conv_speakers[-1] else last
                seg.speaker = assigned
                seg.attribution_source = "turn_taking"
                conv_speakers.append(assigned)
                log.debug("Turn-taking: segment %d → %s", seg.id, assigned)
            elif len(conv_speakers) == 1:
                # Only one known speaker — can't alternate.
                # Leave as unknown for AI attribution.
                pass
