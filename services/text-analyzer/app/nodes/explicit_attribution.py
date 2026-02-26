"""Node 2 — Explicit Attribution.

Scans narration segments adjacent to dialogue for speech-verb patterns
like "said Elena", "Marcus whispered", "she asked", etc.  Assigns the
extracted character name to the dialogue segment's ``speaker`` field.

Pronoun-only attributions ("he said") are tagged with
``attribution_source="pronoun_<gender>"`` for downstream resolution.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

from ..models import Segment

log = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Words that look like capitalised names but aren't.
_NON_NAMES = frozenset({
    "the", "this", "that", "these", "those", "there", "then", "than",
    "they", "them", "their", "what", "when", "where", "which", "while",
    "who", "whom", "whose", "with", "will", "would", "could", "should",
    "have", "has", "had", "been", "being", "does", "did", "done",
    "from", "into", "onto", "upon", "after", "before", "above", "below",
    "about", "again", "also", "another", "because", "between", "both",
    "but", "each", "even", "every", "for", "here", "how", "just",
    "like", "more", "most", "much", "never", "not", "now", "only",
    "other", "over", "some", "still", "such", "through", "under",
    "very", "well", "were", "why", "and", "are", "can", "her",
    "him", "his", "its", "may", "nor", "our", "out", "own", "per",
    "too", "two", "was", "yet", "all", "any", "few", "got", "get",
    "let", "may", "new", "old", "one", "say", "see", "set", "way",
})

_MALE_PRONOUNS = frozenset({"he"})
_FEMALE_PRONOUNS = frozenset({"she"})


def _load_speech_verbs() -> set[str]:
    path = _DATA_DIR / "speech_verbs.txt"
    verbs: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            verbs.add(line.lower())
    return verbs


# Module-level cache.
_SPEECH_VERBS: set[str] | None = None


def _get_speech_verbs() -> set[str]:
    global _SPEECH_VERBS
    if _SPEECH_VERBS is None:
        _SPEECH_VERBS = _load_speech_verbs()
    return _SPEECH_VERBS


def attribute_explicit(segments: list[Segment]) -> list[Segment]:
    """Try to resolve each ``speaker="unknown"`` dialogue segment by
    scanning adjacent narration for speech-verb attribution patterns.

    Modifies segments in place and returns the same list.
    """
    verbs = _get_speech_verbs()
    verb_pat = "|".join(re.escape(v) for v in sorted(verbs, key=len, reverse=True))

    # Pre-compile patterns.
    # Pattern A: "verb Name" — "said Elena", "whispered the old man"
    pat_verb_name = re.compile(
        rf"\b({verb_pat})\s+((?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)",
        re.IGNORECASE,
    )
    # Pattern B: "Name verb" — "Elena said", "The wizard murmured"
    pat_name_verb = re.compile(
        rf"((?:the\s+)?[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\s+({verb_pat})\b",
        re.IGNORECASE,
    )
    # Pattern C: pronoun + verb — "he said", "she whispered"
    pat_pronoun_verb = re.compile(
        rf"\b(he|she)\s+({verb_pat})\b",
        re.IGNORECASE,
    )
    # Pattern D: verb + pronoun is rare but possible — "said he"
    pat_verb_pronoun = re.compile(
        rf"\b({verb_pat})\s+(he|she)\b",
        re.IGNORECASE,
    )

    for i, seg in enumerate(segments):
        if seg.kind != "dialogue" or seg.speaker != "unknown":
            continue

        # Gather adjacent narration text.
        context_parts: list[str] = []
        if i > 0 and segments[i - 1].kind == "narration":
            context_parts.append(segments[i - 1].original_text)
        if i < len(segments) - 1 and segments[i + 1].kind == "narration":
            context_parts.append(segments[i + 1].original_text)

        if not context_parts:
            continue

        context = " ".join(context_parts)

        # Try named patterns first.
        name = _try_named_match(context, pat_verb_name, group_idx=2)
        if not name:
            name = _try_named_match(context, pat_name_verb, group_idx=1)

        if name:
            seg.speaker = name
            seg.attribution_source = "explicit"
            log.debug("Explicit attribution: segment %d → %s", seg.id, name)
            continue

        # Fall back to pronoun patterns.
        pronoun = _try_pronoun_match(context, pat_pronoun_verb, pat_verb_pronoun)
        if pronoun:
            gender = "male" if pronoun.lower() in _MALE_PRONOUNS else "female"
            seg.attribution_source = f"pronoun_{gender}"
            log.debug("Pronoun attribution: segment %d → %s", seg.id, gender)

    return segments


def _try_named_match(
    text: str, pattern: re.Pattern, group_idx: int
) -> Optional[str]:
    """Return the first valid character name matched by *pattern*, or None."""
    for m in pattern.finditer(text):
        candidate = m.group(group_idx).strip()
        # Strip leading "the " if present.
        if candidate.lower().startswith("the "):
            candidate = candidate[4:]
        # Filter non-names.
        if candidate.lower() in _NON_NAMES:
            continue
        if len(candidate) < 2:
            continue
        return candidate
    return None


def _try_pronoun_match(
    text: str,
    pat_pronoun_verb: re.Pattern,
    pat_verb_pronoun: re.Pattern,
) -> Optional[str]:
    """Return the pronoun ("he"/"she") if a pronoun+verb pattern matches."""
    m = pat_pronoun_verb.search(text)
    if m:
        return m.group(1)
    m = pat_verb_pronoun.search(text)
    if m:
        return m.group(2)
    return None
