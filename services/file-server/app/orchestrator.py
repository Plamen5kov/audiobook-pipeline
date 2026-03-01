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

log = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")

# Concurrency limit for parallel TTS calls.
TTS_CONCURRENCY = int(os.getenv("TTS_CONCURRENCY", "3"))

# Internal service URLs.
TEXT_ANALYZER_URL = os.getenv("TEXT_ANALYZER_URL", "http://text-analyzer:8001")
TTS_ROUTER_URL = os.getenv("TTS_ROUTER_URL", "http://tts-router:8010")
AUDIO_ASSEMBLY_URL = os.getenv("AUDIO_ASSEMBLY_URL", "http://audio-assembly:8005")


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

        # Status: analyzing → done
        await _write_status(job_id, {
            "phase": "analyzing",
            "status": "done",
            "job_id": job_id,
            "segments": segments,
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
) -> None:
    """Run parallel TTS → audio-assembly, writing status updates at each
    stage so the frontend can show live progress."""
    total = len(segments)
    tts_started = _now()

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
                "text": seg.get("original_text", ""),
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
        output_filename = f"chapter_{int(time.time() * 1000)}.wav"

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

        # ── Status: done ───────────────────────────────────────────
        await _write_status(job_id, {
            "phase": "done",
            "status": "done",
            "job_id": job_id,
            "output_file": output_file,
            "clips": clips,
            "voice_mapping": voice_mapping,
            "engine_mapping": engine_mapping,
            "nodes": {
                "text-analyzer": {"status": "done"},
                "tts-router": {"status": "done", "started": tts_started, "finished": aa_started},
                "audio-assembly": {"status": "done", "started": aa_started, "finished": aa_finished},
            },
        })

        log.info("synthesize complete: job_id=%s output=%s", job_id, output_file)

        # ── Post-assembly cleanup ──────────────────────────────────
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
