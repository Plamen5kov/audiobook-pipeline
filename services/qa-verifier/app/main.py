"""QA Verifier — transcribes synthesised audio and compares it to the source text.

Catches the failure modes you cannot hear without listening to the whole chapter:
a segment that was dropped, truncated, or where the model went off-script.

Verification is per segment rather than whole-file, because a chapter-level
score tells you something is wrong but not where. Each segment's WAV already
exists on disk, so this only costs transcription.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

import torch
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from transformers import pipeline

from core.verification.similarity import normalise, similarity

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

MODEL_ID = os.getenv("WHISPER_MODEL", "openai/whisper-small.en")
# Below this, a segment is reported as failed.
DEFAULT_THRESHOLD = float(os.getenv("QA_THRESHOLD", "0.85"))

asr = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global asr
    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    log.info("loading ASR model: %s on %s", MODEL_ID, device)
    asr = pipeline(
        "automatic-speech-recognition",
        model=MODEL_ID,
        device=device,
        torch_dtype=torch.float16 if device.startswith("cuda") else torch.float32,
    )
    log.info("ASR model loaded")
    yield


app = FastAPI(title="QA Verifier", lifespan=lifespan)


class SegmentCheck(BaseModel):
    id: int
    text: str
    file_path: str


class VerifyRequest(BaseModel):
    segments: list[SegmentCheck]
    threshold: float = DEFAULT_THRESHOLD
    # Below this word count a low score is reported as "suspect" rather than
    # "failed" — too short for the score to mean much.
    min_words_for_failure: int = 4


@app.get("/health")
def health():
    return {"status": "ok", "model": MODEL_ID, "cuda": torch.cuda.is_available()}


@app.post("/verify")
def verify(request: VerifyRequest):
    if asr is None:
        raise HTTPException(status_code=503, detail="ASR model not loaded")

    results = []
    missing = []
    for seg in request.segments:
        if not os.path.exists(seg.file_path):
            missing.append(seg.id)
            continue
        try:
            out = asr(seg.file_path)
            heard = (out or {}).get("text", "").strip()
        except Exception as exc:
            log.error("transcription failed: segment=%d error=%s", seg.id, exc)
            raise HTTPException(status_code=500, detail=f"Transcription failed on segment {seg.id}: {exc}")

        score = similarity(seg.text, heard)
        words = len(normalise(seg.text))
        results.append({
            "id": seg.id,
            "similarity": round(score, 4),
            "words": words,
            "passed": score >= request.threshold,
            "expected": seg.text,
            "heard": heard,
        })

    # A short segment carries no statistical weight: one mis-transcribed proper
    # noun ("Bingley" -> "Bing Li") sinks the score on its own. Report those
    # separately so genuine dropouts are not buried in name-related noise.
    below = [r for r in results if not r["passed"]]
    failed = [r for r in below if r["words"] >= request.min_words_for_failure]
    suspect = [r for r in below if r["words"] < request.min_words_for_failure]

    checked = len(results)
    mean = round(sum(r["similarity"] for r in results) / checked, 4) if checked else 0.0
    log.info("verify: checked=%d passed=%d failed=%d suspect=%d mean=%.3f missing=%d",
             checked, checked - len(below), len(failed), len(suspect), mean, len(missing))

    return {
        "checked": checked,
        "passed": checked - len(below),
        "failed_count": len(failed),
        "suspect_count": len(suspect),
        "mean_similarity": mean,
        "threshold": request.threshold,
        "missing_files": missing,
        # Worst first, so the caller sees the most broken segments immediately.
        "failed": sorted(failed, key=lambda r: r["similarity"]),
        "suspect": sorted(suspect, key=lambda r: r["similarity"]),
    }
