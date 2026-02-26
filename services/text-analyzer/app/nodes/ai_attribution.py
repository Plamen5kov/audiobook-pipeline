"""Node 7 — AI Attribution.

Resolves remaining ``speaker="unknown"`` dialogue segments by calling a
small LLM with targeted context.  The LLM receives only the surrounding
segments and the character list — it returns only speaker names, never
generates text.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from pathlib import Path

from ..models import Segment
from ..timing import timed_node
from .ollama_client import call_ollama

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "ai_attribution_system.txt").read_text().strip()
_USER_TEMPLATE = (_PROMPTS_DIR / "ai_attribution_user.txt").read_text().strip()

# Maximum unknown segments per LLM batch.
_BATCH_SIZE = 20
# How many segments of context to include around each unknown.
_CONTEXT_WINDOW = 3


@timed_node("ai_attribution", "ai")
async def resolve_ambiguous_speakers(
    segments: list[Segment],
    characters: list[dict],
    ollama_url: str,
    model_name: str,
) -> list[Segment]:
    """Call the LLM for any dialogue segments still marked ``speaker="unknown"``.

    Modifies segments in place and returns the same list.
    """
    unknown_indices = [
        i for i, s in enumerate(segments)
        if s.kind == "dialogue" and s.speaker == "unknown"
    ]

    if not unknown_indices:
        log.info("AI attribution: nothing to resolve (0 unknown)")
        return segments

    character_names = [c["name"] for c in characters if c["name"] != "narrator"]
    log.info("AI attribution: resolving %d unknown segment(s) against characters %s",
             len(unknown_indices), character_names)

    # If there are no known characters at all, the LLM needs to discover them.
    # Pass segment context so the LLM can infer names from narration.
    if not character_names:
        # Extract candidate names from narration (capitalised words that appear
        # multiple times) as hints for the LLM.
        character_names = _extract_candidate_names(segments)
        log.info("AI attribution: no characters from registry, inferred candidates: %s",
                 character_names)

    # Build query batches.
    for batch_start in range(0, len(unknown_indices), _BATCH_SIZE):
        batch_indices = unknown_indices[batch_start:batch_start + _BATCH_SIZE]
        queries = []

        for idx in batch_indices:
            ctx_start = max(0, idx - _CONTEXT_WINDOW)
            ctx_end = min(len(segments), idx + _CONTEXT_WINDOW + 1)
            context = [
                {
                    "id": s.id,
                    "kind": s.kind,
                    "speaker": s.speaker,
                    "text": s.original_text[:200],
                }
                for s in segments[ctx_start:ctx_end]
            ]
            queries.append({
                "segment_id": segments[idx].id,
                "dialogue_text": segments[idx].original_text,
                "context": context,
            })

        prompt = _USER_TEMPLATE.format(
            character_names=json.dumps(character_names),
            queries=json.dumps(queries, indent=2),
        )

        try:
            attributions = await _request_attributions(ollama_url, model_name, prompt)
        except Exception:
            log.exception("AI attribution LLM call failed")
            attributions = []

        # Apply attributions from this batch.
        attr_map = {a["segment_id"]: a["speaker"] for a in attributions}
        for idx in batch_indices:
            seg = segments[idx]
            speaker = attr_map.get(seg.id)
            if speaker:
                seg.speaker = speaker
                seg.attribution_source = "ai"
                log.debug("AI attribution: segment %d → %s", seg.id, speaker)

    # Fallback: any still-unknown segments get assigned to the last known speaker.
    _fallback_last_speaker(segments)

    return segments


async def _request_attributions(
    ollama_url: str, model_name: str, prompt: str
) -> list[dict]:
    """Send a single LLM call and return the ``attributions`` list."""
    parsed = await call_ollama(ollama_url, model_name, _SYSTEM_PROMPT, prompt)
    return parsed.get("attributions", [])


def _fallback_last_speaker(segments: list[Segment]) -> None:
    """Assign any remaining unknown dialogue to the most recent known speaker."""
    last_speaker: str | None = None
    for seg in segments:
        if seg.kind == "dialogue" and seg.speaker not in ("unknown", "narrator"):
            last_speaker = seg.speaker
        elif seg.kind == "dialogue" and seg.speaker == "unknown" and last_speaker:
            seg.speaker = last_speaker
            seg.attribution_source = "default"
            log.debug("Fallback attribution: segment %d → %s", seg.id, last_speaker)


def _extract_candidate_names(segments: list[Segment]) -> list[str]:
    """Extract likely character names from narration when no characters
    were found by the explicit attribution node."""
    # Common words that get capitalised mid-sentence but aren't names.
    _COMMON = {
        "The", "There", "Their", "They", "These", "This", "That", "Those",
        "Then", "Than", "When", "Where", "What", "Which", "While", "Who",
        "How", "Here", "His", "Her", "Its", "Our", "But", "And", "For",
        "Not", "All", "Can", "Has", "Had", "Was", "Were", "Are", "Did",
        "Does", "May", "Most", "Much", "Many", "Some", "Just", "Also",
        "From", "Into", "With", "After", "Before", "About", "Still",
        "Even", "Only", "Very", "Each", "Every", "Both", "Such",
        "Instead", "Mostly", "Sunburn", "Looking", "Glancing",
    }

    # Find capitalised words in narration that aren't sentence-initial.
    name_counts: Counter[str] = Counter()
    for seg in segments:
        if seg.kind != "narration":
            continue
        # Look for capitalised words NOT at the start of a sentence.
        for m in re.finditer(r"(?<=[a-z.,;!?\u2019]\s)([A-Z][a-z]{2,})", seg.original_text):
            word = m.group(1)
            if word not in _COMMON:
                name_counts[word] += 1

    # Return names that appear more than once (likely character names, not place names).
    return [name for name, count in name_counts.most_common(10) if count >= 2]
