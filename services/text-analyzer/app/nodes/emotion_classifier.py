"""Node 8 — Emotion Classifier.

Classifies emotion for every segment using batched LLM calls via Ollama.
Each batch sends up to 30 segments and receives emotion + intensity back.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import httpx

from ..models import ALLOWED_EMOTIONS, Segment

log = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
_SYSTEM_PROMPT = (_PROMPTS_DIR / "emotion_system.txt").read_text().strip()
_USER_TEMPLATE = (_PROMPTS_DIR / "emotion_user.txt").read_text().strip()

_BATCH_SIZE = 30


async def classify_emotions(
    segments: list[Segment],
    ollama_url: str,
    model_name: str,
) -> list[Segment]:
    """Classify emotion for every segment using batched LLM calls.

    Modifies segments in place and returns the same list.
    """
    if not segments:
        return segments

    log.info("Emotion classifier: classifying %d segments in batches of %d",
             len(segments), _BATCH_SIZE)

    for batch_start in range(0, len(segments), _BATCH_SIZE):
        batch = segments[batch_start:batch_start + _BATCH_SIZE]

        items = [
            {
                "id": s.id,
                "speaker": s.speaker,
                "text": s.original_text[:300],
            }
            for s in batch
        ]

        prompt = _USER_TEMPLATE.format(
            allowed_emotions=json.dumps(list(ALLOWED_EMOTIONS)),
            segments=json.dumps(items, indent=2),
        )

        try:
            emotion_map = await _call_ollama(ollama_url, model_name, prompt)
        except Exception:
            log.exception("Emotion classifier LLM call failed for batch starting at %d",
                          batch_start)
            emotion_map = {}

        for seg in batch:
            if seg.id in emotion_map:
                emotion, intensity = emotion_map[seg.id]
                if emotion in ALLOWED_EMOTIONS:
                    seg.emotion = emotion
                    seg.intensity = max(0.0, min(1.0, intensity))

    return segments


async def _call_ollama(
    ollama_url: str, model_name: str, prompt: str
) -> dict[int, tuple[str, float]]:
    """Send a batched emotion classification request to Ollama.

    Returns a dict mapping segment ID → (emotion, intensity).
    """
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model_name,
                "prompt": prompt,
                "system": _SYSTEM_PROMPT,
                "stream": False,
                "format": "json",
                "options": {"num_predict": -1},
            },
        )
        resp.raise_for_status()

    raw = resp.json().get("response", "")
    parsed = json.loads(raw)
    emotions = parsed.get("emotions", [])

    result: dict[int, tuple[str, float]] = {}
    for entry in emotions:
        if not isinstance(entry, dict):
            continue
        seg_id = entry.get("id")
        emotion = entry.get("emotion", "neutral")
        intensity = entry.get("intensity", 0.5)
        if isinstance(seg_id, int) and emotion in ALLOWED_EMOTIONS:
            result[seg_id] = (emotion, float(intensity))

    return result
