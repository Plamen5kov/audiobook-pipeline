"""Job status: what the pipeline is doing, for the frontend to poll."""

import json
import logging
import os

import aiofiles
from fastapi import APIRouter, HTTPException, Request

from ..config import OUTPUT_DIR, safe_filename

log = logging.getLogger(__name__)
router = APIRouter(prefix="/status", tags=["status"])


async def read_status_file(job_id: str) -> dict | None:
    """Read a job's status JSON, or None if it is absent or unreadable."""
    path = os.path.join(OUTPUT_DIR, f"status_{safe_filename(job_id)}.json")
    if not os.path.exists(path):
        return None
    try:
        async with aiofiles.open(path, "r") as f:
            return json.loads(await f.read())
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("could not read status for %s: %s", job_id, exc)
        return None


@router.post("/{job_id}")
async def write_status(job_id: str, request: Request):
    """Write a job status update (called by the orchestrator or external tools)."""
    job_id = safe_filename(job_id)
    data = await request.json()
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(data))
    log.info("Status written: job_id=%s phase=%s status=%s",
             job_id, data.get("phase"), data.get("status"))
    return {"ok": True}


@router.get("/{job_id}")
async def read_status(job_id: str):
    """Read current job status (polled by the frontend)."""
    data = await read_status_file(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return data
