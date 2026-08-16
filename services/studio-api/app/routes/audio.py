"""Serving generated audio, with range requests so a browser can seek."""

import os
import re

import aiofiles
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..config import OUTPUT_DIR, safe_filename

router = APIRouter(prefix="/audio", tags=["audio"])

_MEDIA_TYPES = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".m4b": "audio/mp4",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
}


@router.get("/{filename}")
async def get_audio(filename: str, request: Request):
    """Serve a generated audiobook file, honouring Range so seeking and
    duration work in the browser."""
    filename = safe_filename(filename)
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")

    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")
    media_type = _MEDIA_TYPES.get(
        os.path.splitext(filename)[1].lower(), "application/octet-stream"
    )

    async def stream(start: int, length: int):
        async with aiofiles.open(path, "rb") as f:
            await f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = await f.read(min(65536, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    if range_header:
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            length = end - start + 1
            return StreamingResponse(
                stream(start, length),
                status_code=206,
                media_type=media_type,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                },
            )

    return StreamingResponse(
        stream(0, file_size),
        media_type=media_type,
        headers={"Accept-Ranges": "bytes", "Content-Length": str(file_size)},
    )
