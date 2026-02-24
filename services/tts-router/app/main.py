import json
import logging
import os

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="TTS Router",
    description="Routes /synthesize requests to the correct TTS backend based on the 'engine' field.",
)

# Backend map: engine name → base URL. Loaded from TTS_BACKENDS env var (JSON string).
# Example: '{"xtts-v2":"http://xtts-v2:8003","qwen3-tts":"http://qwen3-tts:8007"}'
BACKENDS: dict[str, str] = json.loads(os.getenv("TTS_BACKENDS", "{}"))
DEFAULT_ENGINE: str = os.getenv("DEFAULT_ENGINE", "xtts-v2")


@app.on_event("startup")
async def startup():
    if not BACKENDS:
        log.warning("TTS_BACKENDS env var is empty — no backends configured")
    else:
        log.info("TTS backends loaded:")
        for engine, url in BACKENDS.items():
            log.info("  %s -> %s", engine, url)
    log.info("Default engine: %s", DEFAULT_ENGINE)


@app.post("/synthesize")
async def synthesize(request: Request):
    body_bytes = await request.body()

    try:
        body_json = json.loads(body_bytes)
    except Exception:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON")

    engine = body_json.get("engine") or DEFAULT_ENGINE
    backend_base = BACKENDS.get(engine)

    if not backend_base:
        # Fall back to default engine if the requested one isn't registered
        backend_base = BACKENDS.get(DEFAULT_ENGINE)
        if not backend_base:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown engine {engine!r} and no default backend configured",
            )
        log.warning("Unknown engine %r, falling back to %r", engine, DEFAULT_ENGINE)

    backend_url = f"{backend_base}/synthesize"
    log.info(
        "Routing segment_id=%s speaker=%r engine=%r -> %s",
        body_json.get("segment_id", "?"),
        body_json.get("speaker", "?"),
        engine,
        backend_url,
    )

    try:
        async with httpx.AsyncClient(timeout=1200.0) as client:
            resp = await client.post(
                backend_url,
                content=body_bytes,
                headers={"Content-Type": "application/json"},
            )
    except httpx.ConnectError as e:
        log.error("Cannot reach backend %s (engine=%r): %s", backend_url, engine, e)
        raise HTTPException(
            status_code=503, detail=f"TTS backend unreachable: {backend_url} ({e})"
        )
    except httpx.TimeoutException as e:
        log.error("Timeout calling backend %s (engine=%r): %s", backend_url, engine, e)
        raise HTTPException(
            status_code=504, detail=f"TTS backend timeout: {backend_url}"
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "default_engine": DEFAULT_ENGINE,
        "backends": BACKENDS,
    }
