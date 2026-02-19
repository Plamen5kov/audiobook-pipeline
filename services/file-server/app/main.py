import json
import logging
import os
import re

import aiofiles
import httpx
from fastapi import FastAPI, HTTPException, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
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


@app.get("/voices")
async def list_voices():
    """List all WAV files available as reference voices."""
    try:
        files = sorted(f for f in os.listdir(VOICES_DIR) if f.endswith(".wav"))
    except FileNotFoundError:
        return []
    return [{"name": f[:-4], "filename": f} for f in files]


@app.post("/voices/upload")
async def upload_voice(file: UploadFile = File(...)):
    """Upload a WAV file to use as a reference voice."""
    if not file.filename or not file.filename.lower().endswith(".wav"):
        raise HTTPException(status_code=400, detail="Only .wav files are accepted")

    safe_name = re.sub(r"[^a-zA-Z0-9_\-]", "_", os.path.splitext(file.filename)[0])
    dest = os.path.join(VOICES_DIR, f"{safe_name}.wav")

    async with aiofiles.open(dest, "wb") as out:
        content = await file.read()
        await out.write(content)

    log.info("Uploaded voice: %s → %s", file.filename, dest)
    return {"name": safe_name, "filename": f"{safe_name}.wav"}


@app.get("/voices/{filename}")
async def get_voice(filename: str):
    """Serve a reference voice file (for in-browser preview)."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = os.path.join(VOICES_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Voice file not found: {filename}")
    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.get("/audio/{filename}")
async def get_audio(filename: str):
    """Serve a generated audiobook file from the output directory."""
    if ".." in filename or "/" in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")

    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail=f"Audio file not found: {filename}")

    return FileResponse(path, media_type="audio/wav", filename=filename)


@app.post("/status/{job_id}")
async def write_status(job_id: str, request: Request):
    """Write a job status update (called by n8n workflows)."""
    if ".." in job_id or "/" in job_id:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    data = await request.json()
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    async with aiofiles.open(path, "w") as f:
        await f.write(json.dumps(data))
    log.info("Status written: job_id=%s phase=%s status=%s", job_id, data.get("phase"), data.get("status"))
    return {"ok": True}


@app.get("/status/{job_id}")
async def read_status(job_id: str):
    """Read current job status (polled by the frontend)."""
    if ".." in job_id or "/" in job_id:
        raise HTTPException(status_code=400, detail="Invalid job_id")
    path = os.path.join(OUTPUT_DIR, f"status_{job_id}.json")
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Job not found")
    async with aiofiles.open(path, "r") as f:
        content = await f.read()
    return json.loads(content)


@app.post("/api/analyze")
async def proxy_analyze(request: Request):
    """Proxy POST to n8n /webhook/analyze — avoids CORS issues in the browser."""
    body = await request.body()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{N8N_URL}/webhook/analyze",
                              content=body, headers={"Content-Type": "application/json"})
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.post("/api/synthesize")
async def proxy_synthesize(request: Request):
    """Proxy POST to n8n /webhook/synthesize — avoids CORS issues in the browser."""
    body = await request.body()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(f"{N8N_URL}/webhook/synthesize",
                              content=body, headers={"Content-Type": "application/json"})
    return JSONResponse(content=r.json(), status_code=r.status_code)


@app.get("/health")
async def health():
    return {"status": "ok"}


# Serve the frontend — mounted last so API routes take priority
if os.path.isdir(STATIC_DIR):
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
