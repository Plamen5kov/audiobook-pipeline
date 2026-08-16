"""Pipeline orchestrator.

Two async functions drive the pipeline end-to-end, writing status
updates directly via the write_status helper so the frontend can poll
for progress.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from typing import Any

import aiofiles
import httpx

from .autocast import build_voice_mapping

log = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")

# Concurrency limit for parallel TTS calls.
TTS_CONCURRENCY = int(os.getenv("TTS_CONCURRENCY", "3"))

# Internal service URLs.
TEXT_ANALYZER_URL = os.getenv("TEXT_ANALYZER_URL", "http://text-analyzer:8001")
TTS_ROUTER_URL = os.getenv("TTS_ROUTER_URL", "http://tts-router:8010")
AUDIO_ASSEMBLY_URL = os.getenv("AUDIO_ASSEMBLY_URL", "http://audio-assembly:8005")
QA_VERIFIER_URL = os.getenv("QA_VERIFIER_URL", "http://qa-verifier:8006")

# QA runs between assembly and cleanup, since verification is per segment and
# cleanup deletes the per-segment audio.
QA_ENABLED = os.getenv("QA_ENABLED", "true").lower() not in ("false", "0", "no")
QA_TIMEOUT_S = float(os.getenv("QA_TIMEOUT_S", "3600"))
# Keep the per-segment audio when QA flags failures, so the bad clips can be
# listened to instead of being deleted with everything else.
QA_KEEP_INTERMEDIATE_ON_FAIL = os.getenv(
    "QA_KEEP_INTERMEDIATE_ON_FAIL", "true").lower() not in ("false", "0", "no")


# ---------------------------------------------------------------------------
# Status helper
# ---------------------------------------------------------------------------

async def _write_status(job_id: str, data: dict) -> None:
    """Persist a status JSON file for the frontend to poll."""
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(data))
    log.info("status: job_id=%s phase=%s status=%s", job_id, data.get("phase"), data.get("status"))


def _cleanup_old_status(current_job_id: str) -> None:
    """Remove status files from previous jobs (called once at the start of a new pipeline run)."""
    current = f"status_{current_job_id}.json"
    for fname in os.listdir(OUTPUT_DIR):
        if fname.startswith("status_") and fname.endswith(".json") and fname != current:
            try:
                os.remove(os.path.join(OUTPUT_DIR, fname))
            except OSError:
                pass


def _now() -> int:
    return math.floor(time.time())


# ---------------------------------------------------------------------------
# Analyze pipeline
# ---------------------------------------------------------------------------

async def run_analyze(
    client: httpx.AsyncClient,
    job_id: str,
    title: str,
    text: str,
) -> None:
    """Call text-analyzer and write status updates."""
    _cleanup_old_status(job_id)
    started = _now()

    try:
        # Status: analyzing → running
        await _write_status(job_id, {
            "phase": "analyzing",
            "status": "running",
            "job_id": job_id,
            "nodes": {
                "text-analyzer": {"status": "running", "started": started},
            },
        })

        # Call text-analyzer
        resp = await client.post(
            f"{TEXT_ANALYZER_URL}/analyze",
            json={"title": title, "text": text},
        )
        resp.raise_for_status()
        result = resp.json()

        finished = _now()
        segments = result.get("segments", [])
        characters = result.get("characters", [])

        # Status: analyzing → done
        await _write_status(job_id, {
            "phase": "analyzing",
            "status": "done",
            "job_id": job_id,
            "segments": segments,
            # Carried through so voice assignment can use the inferred gender.
            "characters": characters,
            "title": title,
            "nodes": {
                "text-analyzer": {"status": "done", "started": started, "finished": finished},
            },
        })

        log.info("analyze complete: job_id=%s segments=%d", job_id, len(segments))

    except Exception as exc:
        log.error("analyze failed: job_id=%s error=%s", job_id, exc)
        await _write_status(job_id, {
            "phase": "analyzing",
            "status": "error",
            "job_id": job_id,
            "error": str(exc),
            "nodes": {
                "text-analyzer": {"status": "error", "started": started},
            },
        })


# ---------------------------------------------------------------------------
# Synthesize pipeline
# ---------------------------------------------------------------------------

async def run_synthesize(
    client: httpx.AsyncClient,
    job_id: str,
    segments: list[dict[str, Any]],
    voice_mapping: dict[str, str],
    engine_mapping: dict[str, str],
    characters: list[dict[str, Any]] | None = None,
) -> None:
    """Run parallel TTS → audio-assembly, writing status updates at each
    stage so the frontend can show live progress."""
    total = len(segments)
    tts_started = _now()

    # Any speaker the caller did not cast gets a gender-appropriate voice that
    # stays fixed for the whole chapter. Explicit choices are preserved.
    speakers = sorted({s.get("speaker", "default") for s in segments})
    voice_mapping = build_voice_mapping(characters or [], speakers, voice_mapping)

    try:
        # ── Status: synthesizing → running (tts-router) ───────────
        await _write_status(job_id, {
            "phase": "synthesizing",
            "status": "running",
            "job_id": job_id,
            "total": total,
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "running", "started": tts_started, "completed": 0, "total": total},
                "audio-assembly": {"status": "pending"},
            },
        })

        # ── Apply voice mapping ────────────────────────────────────
        tts_requests: list[dict[str, Any]] = []
        for seg in segments:
            speaker = seg.get("speaker", "default")
            engine = engine_mapping.get(speaker, "xtts-v2")
            voice_value = voice_mapping.get(speaker, "")
            reference_audio_path = (
                f"/voices/xtts/{voice_value or 'generic_neutral.wav'}"
                if engine != "qwen3-tts"
                else ""
            )
            qwen_speaker = voice_value if engine == "qwen3-tts" else ""

            tts_requests.append({
                "segment_id": seg["id"],
                "speaker": speaker,
                "text": seg.get("spoken_text") or seg.get("original_text", ""),
                "reference_audio_path": reference_audio_path,
                "engine": engine,
                "qwen_speaker": qwen_speaker,
                "emotion": seg.get("emotion", "neutral"),
                "intensity": seg.get("intensity", 0.5),
            })

        # ── Parallel TTS via semaphore ─────────────────────────────
        semaphore = asyncio.Semaphore(TTS_CONCURRENCY)
        completed_count = 0
        tts_results: list[dict[str, Any]] = [{}] * len(tts_requests)

        async def synthesize_one(idx: int, req: dict[str, Any]) -> None:
            nonlocal completed_count
            async with semaphore:
                resp = await client.post(
                    f"{TTS_ROUTER_URL}/synthesize",
                    json=req,
                )
                resp.raise_for_status()
                tts_results[idx] = resp.json()

                completed_count += 1
                # Update progress
                await _write_status(job_id, {
                    "phase": "synthesizing",
                    "status": "running",
                    "job_id": job_id,
                    "total": total,
                    "nodes": {
                        "text-analyzer": {"status": "done"},
                        "tts-router": {
                            "status": "running",
                            "started": tts_started,
                            "completed": completed_count,
                            "total": total,
                        },
                        "audio-assembly": {"status": "pending"},
                    },
                })

        await asyncio.gather(*[
            synthesize_one(i, req) for i, req in enumerate(tts_requests)
        ])

        aa_started = _now()

        # ── Status: tts done, assembly starting ────────────────────
        await _write_status(job_id, {
            "phase": "synthesizing",
            "status": "done",
            "job_id": job_id,
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "done", "total": total, "started": tts_started, "finished": aa_started},
                "audio-assembly": {"status": "running", "started": aa_started},
            },
        })

        # ── Prepare assembly request ───────────────────────────────
        pause_map: dict[int, int] = {
            seg["id"]: seg.get("pause_before_ms", 0) for seg in segments
        }
        clips = [
            {
                "id": r["segment_id"],
                "file_path": r["file_path"],
                "pause_before_ms": pause_map.get(r["segment_id"], 0),
            }
            for r in tts_results
        ]
        output_filename = f"chapter_{int(time.time() * 1000)}.mp3"

        # ── Audio assembly ─────────────────────────────────────────
        resp = await client.post(
            f"{AUDIO_ASSEMBLY_URL}/assemble",
            json={"clips": clips, "output_filename": output_filename},
        )
        resp.raise_for_status()
        assemble_result = resp.json()

        aa_finished = _now()
        output_file = (
            assemble_result.get("filename")
            or assemble_result.get("output_filename")
            or assemble_result.get("output_file", "")
        )

        # ── QA verification ────────────────────────────────────────
        # Must run before cleanup: verification is per segment, and cleanup
        # deletes the per-segment audio.
        qa_started = _now()
        await _write_status(job_id, {
            "phase": "verifying",
            "status": "running",
            "job_id": job_id,
            "output_file": output_file,
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "done", "started": tts_started, "finished": aa_started},
                "audio-assembly": {"status": "done", "started": aa_started, "finished": aa_finished},
                "qa-verifier": {"status": "running", "started": qa_started},
            },
        })
        qa_report = await _run_qa(client, segments, clips)
        qa_finished = _now()

        # ── Status: done ───────────────────────────────────────────
        await _write_status(job_id, {
            "phase": "done",
            "status": "done",
            "job_id": job_id,
            "output_file": output_file,
            "clips": clips,
            "voice_mapping": voice_mapping,
            "engine_mapping": engine_mapping,
            "qa": qa_report,
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "done", "started": tts_started, "finished": aa_started},
                "audio-assembly": {"status": "done", "started": aa_started, "finished": aa_finished},
                "qa-verifier": {"status": qa_report.get("status", "skipped"),
                                "started": qa_started, "finished": qa_finished},
            },
        })

        log.info("synthesize complete: job_id=%s output=%s qa=%s",
                 job_id, output_file, qa_report.get("status"))

        # ── Post-assembly cleanup ──────────────────────────────────
        if QA_KEEP_INTERMEDIATE_ON_FAIL and qa_report.get("failed_count"):
            log.warning("cleanup: keeping %d intermediate files for inspection "
                        "(%d segments failed QA)", len(clips), qa_report["failed_count"])
        else:
            _cleanup_intermediate(clips)

    except Exception as exc:
        log.error("synthesize failed: job_id=%s error=%s", job_id, exc)
        await _write_status(job_id, {
            "phase": "synthesizing",
            "status": "error",
            "job_id": job_id,
            "error": str(exc),
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "error"},
                "audio-assembly": {"status": "error"},
            },
        })


async def _run_qa(
    client: httpx.AsyncClient,
    segments: list[dict[str, Any]],
    clips: list[dict[str, Any]],
) -> dict[str, Any]:
    """Transcribe each segment and compare it to its source text.

    Never fails the pipeline: a chapter that synthesised fine should still be
    delivered if the verifier is down. The report carries the verdict instead.
    """
    if not QA_ENABLED:
        return {"status": "skipped", "reason": "QA_ENABLED=false"}

    by_id = {s["id"]: s for s in segments}
    checks = [
        {
            "id": c["id"],
            "text": (by_id[c["id"]].get("spoken_text")
                     or by_id[c["id"]].get("original_text", "")),
            "file_path": c["file_path"],
        }
        for c in clips
        if c.get("id") in by_id
    ]
    if not checks:
        return {"status": "skipped", "reason": "no segments to verify"}

    try:
        resp = await client.post(
            f"{QA_VERIFIER_URL}/verify",
            json={"segments": checks},
            timeout=httpx.Timeout(QA_TIMEOUT_S, connect=10.0),
        )
        resp.raise_for_status()
        report = resp.json()
    except Exception as exc:
        log.warning("qa verification unavailable: %s", exc)
        return {"status": "unavailable", "reason": str(exc)}

    report["status"] = "failed" if report.get("failed_count") else "passed"
    log.info("qa: %s — checked=%s passed=%s failed=%s suspect=%s mean=%s",
             report["status"], report.get("checked"), report.get("passed"),
             report.get("failed_count"), report.get("suspect_count"),
             report.get("mean_similarity"))
    return report


def _cleanup_intermediate(clips: list[dict[str, Any]]) -> None:
    """Remove intermediate segment audio files after successful assembly."""
    for clip in clips:
        path = clip.get("file_path", "")
        if path and os.path.exists(path):
            try:
                os.remove(path)
                log.info("cleanup: removed %s", path)
            except OSError as exc:
                log.warning("cleanup: failed to remove %s: %s", path, exc)
