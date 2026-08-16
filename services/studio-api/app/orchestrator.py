"""Pipeline orchestrator.

Two async functions drive the pipeline end-to-end, writing status updates so
the frontend can poll for progress.

Each stage also leaves its result in the job's workspace directory, which is
what makes a run inspectable after the fact rather than only while it is
happening. Synthesis consults the manifest first and renders only the segments
whose text or delivery actually changed, so editing one line costs one line.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time
from pathlib import Path
from typing import Any

import aiofiles
import httpx

from core.casting.voices import build_voice_mapping
from core.jobs.fingerprint import plan, segment_fingerprint

from .config import workspace

log = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")


def _job(job_id: str):
    """The workspace directory for a run."""
    return workspace().job(job_id, create=True)

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
# Keep the per-segment audio after assembly. It is the material for both
# inspection and reuse; the manifest points at it, so discarding it also
# discards the ability to re-render one line instead of a chapter.
KEEP_CLIPS = os.getenv("KEEP_CLIPS", "true").lower() not in ("false", "0", "no")


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

        job = _job(job_id)
        job.write_text("input", "chapter.txt", text)
        job.record_stage("input", "done", artifact="chapter.txt",
                         characters_of_text=len(text))
        job.write_json("analysis", "segments.json",
                       {"title": title, "characters": characters, "segments": segments})
        job.record_stage("analysis", "done", artifact="segments.json",
                         segments=len(segments), characters=len(characters))

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
        _job(job_id).record_stage("analysis", "failed", error=str(exc))
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
    force: set[int] | None = None,
) -> None:
    """Run TTS → audio-assembly, writing status updates at each stage so the
    frontend can show live progress.

    *force* names segments to render again even if nothing about them changed,
    which is how "do that line once more" is expressed when the input is
    identical and only the take was unsatisfying.
    """
    total = len(segments)
    tts_started = _now()

    # Any speaker the caller did not cast gets a gender-appropriate voice that
    # stays fixed for the whole chapter. Explicit choices are preserved.
    speakers = sorted({s.get("speaker", "default") for s in segments})
    voice_mapping = build_voice_mapping(characters or [], speakers, voice_mapping)

    job = _job(job_id)
    job.write_json("cast", "cast.json", {
        "voice_mapping": voice_mapping,
        "engine_mapping": engine_mapping,
        "characters": characters or [],
    })
    job.record_stage("cast", "done", artifact="cast.json", voices=len(voice_mapping))

    def _engine_of(seg):
        return engine_mapping.get(seg.get("speaker", "default"), "xtts-v2")

    def _voice_of(seg):
        return voice_mapping.get(seg.get("speaker", "default"), "")

    # Only render what changed. A segment whose text and delivery are untouched
    # already has a clip, and re-rendering the chapter to fix one line is the
    # thing this exists to avoid.
    to_render, reused = plan(segments, job, _voice_of, _engine_of, force=force or set())
    if reused:
        log.info("synthesize: %d of %d segments already current",
                 len(reused), len(segments))

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
        for seg in to_render:
            speaker = seg.get("speaker", "default")
            engine = _engine_of(seg)
            voice_value = _voice_of(seg)
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
        completed_count = len(reused)
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
        # Clips come from two places now: what was just rendered, and what was
        # already current. Assembly needs them in reading order regardless.
        clip_paths: dict[int, str] = {r["id"]: r["_clip"] for r in reused}
        for seg, result in zip(to_render, tts_results):
            if not result:
                continue
            path = result["file_path"]
            clip_paths[result["segment_id"]] = path
            job.record_segment(result["segment_id"], seg["_fingerprint"], path)
        job.record_stage("synthesis", "done",
                         rendered=len(to_render), reused=len(reused))

        clips = [
            {
                "id": seg["id"],
                "file_path": clip_paths[seg["id"]],
                "pause_before_ms": pause_map.get(seg["id"], 0),
            }
            for seg in segments
            if seg["id"] in clip_paths
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
        job.record_stage("assembly", "done", output=output_file, clips=len(clips))

        qa_report = await _run_qa(client, segments, clips)
        qa_finished = _now()
        job.write_json("qa", "report.json", qa_report)
        job.record_stage("qa", qa_report.get("status", "skipped"),
                         artifact="report.json",
                         failed=qa_report.get("failed_count", 0))

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
        # The per-segment audio is kept. It is what a later run reuses instead
        # of re-rendering, and what you listen to when a line sounds wrong, so
        # deleting it is the expensive choice rather than the tidy one. Set
        # KEEP_CLIPS=false to get the old behaviour, at the cost of making the
        # next run render the whole chapter again.
        if KEEP_CLIPS:
            log.info("keeping %d segment clips for reuse and inspection", len(clips))
        else:
            _cleanup_intermediate(clips)
            for seg in segments:
                job.forget_segment(seg["id"])
            job.save()

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
