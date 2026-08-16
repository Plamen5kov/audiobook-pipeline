"""Starting and steering pipeline runs.

One job at a time, deliberately: the GPU serialises anyway, and two runs
writing the same status file would make the frontend show nonsense.
"""

import asyncio
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from ..orchestrator import run_analyze, run_synthesize
from .status import read_status_file

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["jobs"])

_active_job: dict[str, str] = {}  # {"job_id": ..., "phase": ...} or empty


async def _run_and_clear(coro, job_id: str):
    """Run a pipeline coroutine and clear the active-job lock when it finishes."""
    try:
        await coro
    finally:
        if _active_job.get("job_id") == job_id:
            _active_job.clear()


@router.post("/analyze")
async def api_analyze(request: Request):
    """Start the analysis pipeline as a background task."""
    body = await request.json()
    job_id = body.get("job_id", "")
    title = body.get("title", "Untitled Chapter")
    text = body.get("text", "")

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    if _active_job:
        raise HTTPException(
            status_code=409,
            detail=f"A job is already running ({_active_job.get('phase', 'unknown')}). "
                   "Wait for it to finish.",
        )

    _active_job.update({"job_id": job_id, "phase": "analyzing"})
    client: httpx.AsyncClient = request.app.state.http_client
    asyncio.create_task(_run_and_clear(run_analyze(client, job_id, title, text), job_id))
    return JSONResponse({"status": "analyzing", "job_id": job_id})


@router.post("/synthesize")
async def api_synthesize(request: Request):
    """Start the synthesis pipeline as a background task."""
    body = await request.json()
    job_id = body.get("job_id", "")
    segments = body.get("segments", [])
    voice_mapping = body.get("voice_mapping", {})
    engine_mapping = body.get("engine_mapping", {})
    characters = body.get("characters", [])

    if not job_id:
        raise HTTPException(status_code=400, detail="job_id is required")

    # Fall back to the character list recorded during analysis, so a caller that
    # does not echo it back still gets gender-correct casting.
    if not characters:
        prior = await read_status_file(job_id)
        characters = (prior or {}).get("characters", [])

    # Allow synthesize for the same job that just finished analyzing.
    if _active_job and _active_job.get("job_id") != job_id:
        raise HTTPException(
            status_code=409,
            detail=f"A different job is already running "
                   f"({_active_job.get('phase', 'unknown')}). Wait for it to finish.",
        )

    _active_job.update({"job_id": job_id, "phase": "synthesizing"})
    client: httpx.AsyncClient = request.app.state.http_client
    asyncio.create_task(_run_and_clear(
        run_synthesize(client, job_id, segments, voice_mapping, engine_mapping, characters),
        job_id,
    ))
    return JSONResponse({"status": "synthesizing", "job_id": job_id})


async def _proxy_to_service(url: str, body: bytes, request: Request) -> JSONResponse:
    """Forward a request body to an internal service using the persistent client."""
    client: httpx.AsyncClient = request.app.state.http_client
    try:
        r = await client.post(url, content=body,
                              headers={"Content-Type": "application/json"})
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.HTTPError as exc:
        log.error("Proxy to %s failed: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")


@router.post("/re-synthesize")
async def re_synthesize(request: Request):
    """Proxy a single-segment re-synthesis request to tts-router."""
    return await _proxy_to_service(
        "http://tts-router:8010/synthesize", await request.body(), request,
    )


@router.post("/re-stitch")
async def re_stitch(request: Request):
    """Proxy a re-assembly request to audio-assembly."""
    return await _proxy_to_service(
        "http://audio-assembly:8005/assemble", await request.body(), request,
    )
