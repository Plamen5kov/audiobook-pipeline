"""Reference voice files: list, upload, preview, delete."""

import logging
import os
import re

import aiofiles
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from ..config import VOICES_DIR, engine_dir, safe_filename

log = logging.getLogger(__name__)
router = APIRouter(prefix="/voices", tags=["voices"])


def _builtin_voices(engine: str) -> set[str]:
    """Read the .builtin manifest for an engine."""
    manifest = os.path.join(VOICES_DIR, engine, ".builtin")
    try:
        with open(manifest) as f:
            return {line.strip() for line in f if line.strip()}
    except FileNotFoundError:
        return set()


@router.get("/{engine}")
async def list_voices(engine: str):
    """List all WAV files available for a given engine."""
    engine_path = engine_dir(engine)
    try:
        files = sorted(f for f in os.listdir(engine_path) if f.endswith(".wav"))
    except FileNotFoundError:
        return []
    builtin = _builtin_voices(engine)
    return [{"name": f[:-4], "filename": f, "builtin": f in builtin} for f in files]


@router.post("/upload/{engine}")
async def upload_voice(engine: str, file: UploadFile = File(...)):
    """Upload a WAV file to use as a reference voice."""
    engine_path = engine_dir(engine)
    os.makedirs(engine_path, exist_ok=True)

    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files are accepted")

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", os.path.splitext(file.filename)[0])
    dest = os.path.join(engine_path, f"{safe_name}.wav")

    async with aiofiles.open(dest, "wb") as out:
        content = await file.read()
        await out.write(content)

    log.info("Uploaded voice: %s → %s", file.filename, dest)
    return {"name": safe_name, "filename": f"{safe_name}.wav"}


@router.get("/{engine}/{filename}")
async def get_voice(engine: str, filename: str):
    """Serve a reference voice file (for in-browser preview)."""
    engine_path = engine_dir(engine)
    filename = safe_filename(filename)
    path = os.path.join(engine_path, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Voice file not found: {engine}/{filename}")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@router.delete("/{engine}/{filename}")
async def delete_voice(engine: str, filename: str):
    """Delete a voice file."""
    engine_path = engine_dir(engine)
    filename = safe_filename(filename)
    path = os.path.join(engine_path, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Voice file not found: {engine}/{filename}")
    if filename in _builtin_voices(engine):
        raise HTTPException(status_code=403, detail="Cannot delete built-in voice")
    os.remove(path)
    log.info("Deleted voice: %s/%s", engine, filename)
    return {"ok": True, "deleted": f"{engine}/{filename}"}
