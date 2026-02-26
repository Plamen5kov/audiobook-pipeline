"""Node 4 — Character Registry.

Builds the ``characters`` list from all discovered speaker attributions.
Tracks approximate gender from pronoun usage in adjacent narration.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from ..models import Segment
from ..timing import timed_node

log = logging.getLogger(__name__)

_MALE_PATTERN = re.compile(r"\b(he|him|his)\b", re.IGNORECASE)
_FEMALE_PATTERN = re.compile(r"\b(she|her|hers)\b", re.IGNORECASE)


@timed_node("character_registry", "programmatic")
def build_character_registry(segments: list[Segment]) -> list[dict]:
    """Collect unique character names from segments and return a list
    in the format expected by the API: ``[{"name": ..., "description": ...}]``.

    Always includes ``narrator`` as the first entry.
    """
    char_info: dict[str, dict] = {}  # name → {"count": int, "gender_votes": Counter}

    for i, seg in enumerate(segments):
        if seg.kind != "dialogue" or seg.speaker in ("unknown", "narrator"):
            continue

        name = seg.speaker
        if name not in char_info:
            char_info[name] = {"count": 0, "gender_votes": Counter()}
        char_info[name]["count"] += 1

        # Check adjacent narration for gendered pronouns.
        for j in (i - 1, i + 1):
            if 0 <= j < len(segments) and segments[j].kind == "narration":
                text = segments[j].original_text
                if _MALE_PATTERN.search(text):
                    char_info[name]["gender_votes"]["male"] += 1
                if _FEMALE_PATTERN.search(text):
                    char_info[name]["gender_votes"]["female"] += 1

    result: list[dict] = [{"name": "narrator", "description": "the narrative voice"}]

    for name, info in char_info.items():
        votes = info["gender_votes"]
        parts = []
        if votes["male"] > votes["female"]:
            parts.append("male")
        elif votes["female"] > votes["male"]:
            parts.append("female")
        parts.append(f"{info['count']} dialogue segment(s)")
        result.append({"name": name, "description": ", ".join(parts)})

    log.info("Character registry: %s",
             [c["name"] for c in result])
    return result
