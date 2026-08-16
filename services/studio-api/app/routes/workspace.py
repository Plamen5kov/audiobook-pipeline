"""Reading what a run produced.

The pipeline keeps every stage's output on disk; these routes are how the
studio sees it. The shape follows what a person actually asks, which is not the
shape of the files: "which runs are there", "how far did this one get", and
above all "show me every line, what it was meant to say, and whether it came
out right", which needs the analysis, the manifest and the QA report joined
together rather than fetched separately.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from core.jobs.fingerprint import clip_exists
from core.jobs.workspace import STAGES, Job, stage_dirname

from ..config import CLIP_ROOTS, workspace

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/jobs", tags=["workspace"])

_CLIP_MEDIA_TYPES = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".flac": "audio/flac"}


def _job(job_id: str) -> Job:
    ws = workspace()
    try:
        job = ws.job(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job id")
    if not job.root.is_dir():
        raise HTTPException(status_code=404, detail=f"No such job: {job_id}")
    return job


def _qa_verdicts(job: Job) -> dict[int, dict]:
    report = job.read_json("qa", "report.json") or {}
    return {r["id"]: r for r in report.get("results", []) if "id" in r}


@router.get("")
async def list_jobs():
    """Every run, and how far each one got."""
    ws = workspace()
    return [ws.job(name).summary() for name in ws.jobs()]


@router.get("/{job_id}")
async def get_job(job_id: str):
    """One run: its stages, and what each produced."""
    job = _job(job_id)
    manifest = job.manifest()
    return {
        **job.summary(),
        "stage_detail": {s: manifest["stages"].get(s) for s in STAGES},
    }


@router.get("/{job_id}/stages/{stage}")
async def get_stage_artifact(job_id: str, stage: str):
    """The JSON a stage produced, as it was written."""
    job = _job(job_id)
    try:
        stage_dirname(stage)
    except KeyError:
        raise HTTPException(status_code=400,
                            detail=f"Unknown stage: {stage}. Known: {', '.join(STAGES)}")

    directory = job.stage_dir(stage)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"Stage {stage} has not run")

    artifacts = {}
    for path in sorted(directory.glob("*.json")):
        artifacts[path.name] = job.read_json(stage, path.name)
    if not artifacts:
        # A stage whose output is audio rather than JSON still has contents
        # worth reporting, so say what is there instead of 404ing.
        return {"stage": stage, "files": sorted(p.name for p in directory.iterdir())}
    return {"stage": stage, "artifacts": artifacts}


@router.get("/{job_id}/segments")
async def list_segments(
    job_id: str,
    failed: bool = Query(False, description="only what QA flagged"),
    speaker: str | None = None,
):
    """Every line, joined with whether it has a clip and what QA made of it.

    This is the view the studio is built on, so it is assembled here rather
    than leaving the frontend to fetch three files and correlate them.
    """
    job = _job(job_id)
    analysis = job.read_json("analysis", "segments.json")
    if not analysis:
        raise HTTPException(status_code=404,
                            detail="This run has no analysis artifact yet")

    verdicts = _qa_verdicts(job)
    out = []
    for seg in analysis["segments"]:
        verdict = verdicts.get(seg["id"])
        if failed and (not verdict or verdict.get("status") not in ("failed", "suspect")):
            continue
        if speaker and seg.get("speaker") != speaker:
            continue
        record = job.segment_record(seg["id"])
        out.append({
            **seg,
            "spoken_text": seg.get("spoken_text") or seg.get("original_text", ""),
            "clip": {
                "present": bool(record) and clip_exists(job, record["clip"]),
                "fingerprint": (record or {}).get("fingerprint"),
                "url": f"/api/jobs/{job_id}/segments/{seg['id']}/audio",
            },
            "qa": verdict,
        })

    return {
        "job_id": job_id,
        "title": analysis.get("title"),
        "total": len(analysis["segments"]),
        "returned": len(out),
        "segments": out,
    }


@router.get("/{job_id}/segments/{segment_id}/audio")
async def get_segment_audio(job_id: str, segment_id: int):
    """The take for one line, to listen to."""
    job = _job(job_id)
    record = job.segment_record(segment_id)
    if not record:
        raise HTTPException(status_code=404, detail=f"No clip recorded for segment {segment_id}")

    raw = Path(record["clip"])
    path = (raw if raw.is_absolute() else job.root / raw).resolve()

    # The manifest is written by this service, but a clip path is still a path,
    # and serving one from outside the directories this service is meant to
    # read would turn a manifest into a file-disclosure primitive.
    if not any(path.is_relative_to(root) for root in CLIP_ROOTS):
        log.warning("refusing clip outside the allowed roots: %s", path)
        raise HTTPException(status_code=403, detail="Clip is outside the workspace")
    if not path.exists():
        raise HTTPException(status_code=404,
                            detail=f"Clip for segment {segment_id} is recorded but missing")

    return FileResponse(
        path,
        media_type=_CLIP_MEDIA_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
    )


@router.post("/{job_id}/redo")
async def redo_segments(job_id: str, body: dict):
    """Mark lines to render again on the next synthesis run.

    Nothing is synthesised here. The clips are forgotten, so the next run
    renders exactly these and reuses the rest, which is what "do that line
    again" means when the text has not changed and only the take was wrong.
    """
    job = _job(job_id)
    ids = body.get("segments")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise HTTPException(status_code=400,
                            detail="Body must be {\"segments\": [<int>, ...]}")

    known = {int(k) for k in job.manifest().get("segments", {})}
    for seg_id in ids:
        job.forget_segment(seg_id)
    job.save()
    return {
        "job_id": job_id,
        "marked": ids,
        "had_no_clip": sorted(set(ids) - known),
    }
