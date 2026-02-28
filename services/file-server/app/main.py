import asyncio
import json
import logging
import os
import re

import aiofiles
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="File Server", description="Serves voice samples and generated audio; accepts voice uploads")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

VOICES_DIR = os.getenv("VOICES_DIR", "/voices")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
STATIC_DIR = os.getenv("STATIC_DIR", "/static")
N8N_URL    = os.getenv("N8N_URL", "http://n8n:5678")


def _safe_filename(name: str) -> str:
    """Validate a user-supplied filename, raising 400 on path traversal."""
    if ".." in name or "/" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


VALID_ENGINES = {"xtts", "qwen3"}


def _engine_dir(engine: str) -> str:
    """Return the subdirectory for a given engine, raising 400 on invalid values."""
    if engine not in VALID_ENGINES:
        raise HTTPException(status_code=400, detail=f"Invalid engine: {engine}")
    return os.path.join(VOICES_DIR, engine)


@app.get("/voices/{engine}")
async def list_voices(engine: str):
    """List all WAV files available for a given engine."""
    engine_path = _engine_dir(engine)
    try:
        files = sorted(f for f in os.listdir(engine_path) if f.endswith(".wav"))
    except FileNotFoundError:
        return []
    return [{"name": f[:-4], "filename": f} for f in files]


@app.post("/voices/upload/{engine}")
async def upload_voice(engine: str, file: UploadFile = File(...)):
    """Upload a WAV file to use as a reference voice."""
    engine_path = _engine_dir(engine)
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


@app.get("/voices/{engine}/{filename}")
async def get_voice(engine: str, filename: str):
    """Serve a reference voice file (for in-browser preview)."""
    engine_path = _engine_dir(engine)
    filename = _safe_filename(filename)
    path = os.path.join(engine_path, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Voice file not found: {engine}/{filename}")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.get("/audio/{filename}")
async def get_audio(filename: str, request: Request):
    """Serve a generated audiobook file with range-request support for browser seek/duration."""
    filename = _safe_filename(filename)
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")

    file_size = os.path.getsize(path)
    range_header = request.headers.get("Range")

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
                media_type="audio/wav",
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(length),
                },
            )

    return StreamingResponse(
        stream(0, file_size),
        media_type="audio/wav",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Length": str(file_size),
        },
    )


@app.post("/status/{job_id}")
async def write_status(job_id: str, request: Request):
    """Write a job status update (called by n8n workflows)."""
    job_id = _safe_filename(job_id)
    data = await request.json()
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(data))
    log.info("Status written: job_id=%s phase=%s status=%s", job_id, data.get("phase"), data.get("status"))

    # Clean up status files from previous jobs.
    current = f"status_{job_id}.json"
    for f in os.listdir(OUTPUT_DIR):
        if f.startswith("status_") and f.endswith(".json") and f != current:
            try:
                os.remove(os.path.join(OUTPUT_DIR, f))
            except OSError:
                pass

    return {"ok": True}


@app.get("/status/{job_id}")
async def read_status(job_id: str):
    """Read current job status (polled by the frontend)."""
    job_id = _safe_filename(job_id)
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Job not found")
    async with aiofiles.open(path, "r") as f:
        content = await f.read()
    return json.loads(content)


async def _proxy_to_n8n(webhook: str, body: bytes) -> JSONResponse:
    """Forward a request body to an n8n webhook and return the response."""
    url = f"{N8N_URL}/webhook/{webhook}"
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, content=body, headers={"Content-Type": "application/json"})
        return JSONResponse(content=r.json(), status_code=r.status_code)
    except httpx.HTTPError as exc:
        log.error("Proxy to %s failed: %s", url, exc)
        raise HTTPException(status_code=502, detail=f"Upstream request failed: {exc}")


@app.post("/api/analyze")
async def proxy_analyze(request: Request):
    """Proxy POST to n8n /webhook/analyze — avoids CORS issues in the browser."""
    return await _proxy_to_n8n("analyze", await request.body())


@app.post("/api/synthesize")
async def proxy_synthesize(request: Request):
    """Proxy POST to n8n /webhook/synthesize — avoids CORS issues in the browser."""
    return await _proxy_to_n8n("synthesize", await request.body())


@app.get("/health")
async def health():
    return {"status": "ok"}


_PIPELINE_SERVICES = {
    "text-analyzer":  "http://text-analyzer:8001/health",
    "script-adapter": "http://script-adapter:8002/health",
    "xtts-v2":        "http://xtts-v2:8003/health",
    "tts-router":     "http://tts-router:8010/health",
    "qwen3-tts":      "http://qwen3-tts:8007/health",
    "audio-assembly": "http://audio-assembly:8005/health",
    "n8n":            f"{N8N_URL}/healthz",
}


@app.get("/services/health")
async def services_health():
    """Fan-out health check to all pipeline services in parallel."""
    async def check(name: str, url: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=3.0) as c:
                r = await c.get(url)
                data = r.json()
                return {"name": name, "status": data.get("status", "ok"), "detail": data}
        except Exception as exc:
            return {"name": name, "status": "error", "detail": str(exc)}

    results = await asyncio.gather(*[check(n, u) for n, u in _PIPELINE_SERVICES.items()])
    return list(results)


# Serve the frontend — mounted last so API routes take priority
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
