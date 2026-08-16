"""Deciding which clips are still current.

Re-rendering a whole chapter to change one line wastes half an hour, and the
only reason to do it is not knowing which clips the change affected. A
fingerprint over everything that determines how a segment sounds answers that:
if it has not moved, the clip on disk is still right.

This is the trick build systems use — hash the inputs, reuse the output — and
it depends on one thing being true, that nothing outside the fingerprint
changes the audio. Two things are deliberately outside it. Whether the model
weights changed is not tracked, so swapping an adapter means clearing the
records rather than trusting them. And the engines are not bit-reproducible, so
two renders of the same input differ; that is fine here, because the question
is "does this need doing again", not "is this identical to last time".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

# Everything that changes how a segment sounds. A field absent here is a field
# a person can change without the clip being re-rendered, so the list is the
# contract.
VOICE_FIELDS = ("speaker", "engine", "voice", "emotion", "intensity", "speed")

DIGEST_CHARS = 16


def fingerprint(spoken_text: str, **voice) -> str:
    """Hash the text and the delivery settings for one segment."""
    payload = {"text": spoken_text or ""}
    for f in VOICE_FIELDS:
        value = voice.get(f)
        if value is not None:
            payload[f] = value
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:DIGEST_CHARS]


def segment_fingerprint(segment: dict, voice: str = "", engine: str = "") -> str:
    """Fingerprint a segment as the orchestrator sees it."""
    return fingerprint(
        segment.get("spoken_text") or segment.get("original_text", ""),
        speaker=segment.get("speaker"),
        engine=engine,
        voice=voice,
        emotion=segment.get("emotion"),
        intensity=segment.get("intensity"),
        speed=segment.get("speed"),
    )


def clip_exists(job, clip: str) -> bool:
    """Is the recorded clip still there? Absolute paths are the synthesiser's
    shared volume; relative ones belong to the job directory."""
    p = Path(clip)
    return (p if p.is_absolute() else job.root / p).exists()


def plan(segments: list[dict], job, voice_of, engine_of,
         force: set[int] | None = None) -> tuple[list[dict], list[dict]]:
    """Split segments into those needing synthesis and those already done.

    A segment is reused only when its fingerprint matches *and* the clip it
    names is still on disk. Trusting the manifest alone would hand assembly a
    path to a file somebody deleted.

    A recorded clip may be absolute, because the synthesiser writes into a
    volume shared with assembly rather than into the job directory, or relative
    to the job root for clips the job owns.
    """
    force = force or set()
    todo: list[dict] = []
    reuse: list[dict] = []

    for seg in segments:
        seg_id = seg["id"]
        fp = segment_fingerprint(seg, voice_of(seg), engine_of(seg))
        record = job.segment_record(seg_id)
        clip_ok = False
        if record and record.get("fingerprint") == fp and seg_id not in force:
            clip_ok = clip_exists(job, record["clip"])
        if clip_ok:
            reuse.append({**seg, "_fingerprint": fp, "_clip": record["clip"]})
        else:
            todo.append({**seg, "_fingerprint": fp})

    return todo, reuse
