"""Assign a voice to every character, respecting gender and staying stable.

Two rules, both from listening to a real chapter:

1. A male character must never get a female voice, or vice versa. This is the
   most audible possible error — more jarring than a wrong-but-plausible voice.
2. A character keeps one voice for the whole chapter. Assignment is therefore a
   pure function of the character list, not of iteration order or segment order.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

# Qwen3-TTS preset voices by gender.
QWEN_MALE = ["Ryan", "Dylan", "Eric", "Aiden", "Uncle_Fu"]
QWEN_FEMALE = ["Serena", "Vivian", "Sohee", "Ono_Anna"]

# Narrator gets a fixed voice rather than one drawn from the character pools, so
# it never collides with a character and never moves when the cast changes.
NARRATOR_VOICE = "Ryan"

# Used when gender could not be determined. Deliberately a male voice: most
# narration-heavy fiction defaults masculine, and an unknown-gender speaker is
# usually an unattributed line from the character already speaking.
NEUTRAL_VOICE = "Eric"


def _pool_for(gender: str) -> list[str]:
    if gender == "female":
        return QWEN_FEMALE
    if gender == "male":
        return QWEN_MALE
    return []


def build_voice_mapping(
    characters: list[dict],
    speakers: list[str],
    existing: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return {speaker: qwen_voice} covering every speaker.

    *existing* entries always win, so an explicit cast from voice-cast.yaml or
    the web UI is never overridden.
    """
    mapping = dict(existing or {})
    gender_of = {c.get("name"): c.get("gender", "unknown") for c in characters}

    used: set[str] = set(mapping.values())
    next_idx = {"male": 0, "female": 0}

    # Cast the narrator first and reserve its voice. Otherwise a character
    # assigned earlier in sort order takes the narrator's voice and the two
    # become indistinguishable — as bad as a wrong-gender voice.
    if "narrator" in speakers and "narrator" not in mapping:
        mapping["narrator"] = NARRATOR_VOICE
    used.add(mapping.get("narrator", NARRATOR_VOICE))

    # Assign in a stable order so the same chapter always casts identically.
    for speaker in sorted(speakers):
        if speaker in mapping:
            continue

        gender = gender_of.get(speaker, "unknown")
        pool = _pool_for(gender)
        if not pool:
            # Prefer the neutral voice, but not if it is already taken.
            choice = NEUTRAL_VOICE if NEUTRAL_VOICE not in used else next(
                (v for v in QWEN_MALE + QWEN_FEMALE if v not in used), NEUTRAL_VOICE)
            mapping[speaker] = choice
            used.add(choice)
            continue

        # Prefer an unused voice from the correct-gender pool; if the pool is
        # exhausted, reuse within it rather than crossing gender.
        choice = None
        for _ in range(len(pool)):
            candidate = pool[next_idx[gender] % len(pool)]
            next_idx[gender] += 1
            if candidate not in used:
                choice = candidate
                break
        if choice is None:
            choice = pool[next_idx[gender] % len(pool)]
            next_idx[gender] += 1
            log.warning("voice pool for %s exhausted; reusing %s for %s",
                        gender, choice, speaker)

        mapping[speaker] = choice
        used.add(choice)

    log.info("autocast: %s", mapping)
    return mapping
