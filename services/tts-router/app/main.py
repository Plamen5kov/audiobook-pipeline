import json
import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="TTS Router",
    description="Routes /synthesize requests to the correct TTS backend based on the 'engine' field.",
)

# Backend map: engine name -> base URL. Loaded from TTS_BACKENDS env var (JSON string).
# Example: '{"xtts-v2":"http://xtts-v2:8003","qwen3-tts":"http://qwen3-tts:8007"}'
BACKENDS: dict[str, str] = json.loads(os.getenv("TTS_BACKENDS", "{}"))
DEFAULT_ENGINE: str = os.getenv("DEFAULT_ENGINE", "xtts-v2")

# Reuse a single async HTTP client across requests to benefit from connection pooling.
_http_client: httpx.AsyncClient | None = None


class SynthesizeRequest(BaseModel):
    """Mirrors the shared TTS contract. Used for validation and structured logging only --
    the raw JSON body is forwarded as-is to the backend so no fields are lost."""
    text: str
    segment_id: int = 0
    speaker: str = "default"
    engine: str = ""
    reference_audio_path: str = ""
    qwen_speaker: str = ""
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0


@app.on_event("startup")
async def startup():
    global _http_client
    _http_client = httpx.AsyncClient(timeout=1200.0)

    if not BACKENDS:
        log.warning("TTS_BACKENDS env var is empty -- no backends configured")
    else:
        log.info("TTS backends loaded:")
        for engine, url in BACKENDS.items():
            log.info("  %s -> %s", engine, url)
    log.info("default_engine=%s", DEFAULT_ENGINE)


@app.on_event("shutdown")
async def shutdown():
    if _http_client:
        await _http_client.aclose()


def _resolve_backend(engine: str) -> tuple[str, str]:
    """Return (resolved_engine, backend_base_url) or raise HTTPException."""
    backend_base = BACKENDS.get(engine)
    if backend_base:
        return engine, backend_base

    # Fall back to default engine if the requested one is not registered.
    fallback_base = BACKENDS.get(DEFAULT_ENGINE)
    if not fallback_base:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown engine {engine!r} and no default backend configured",
        )
    log.warning("engine=%r not found, falling back to engine=%r", engine, DEFAULT_ENGINE)
    return DEFAULT_ENGINE, fallback_base


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    engine = request.engine.strip() or DEFAULT_ENGINE
    resolved_engine, backend_base = _resolve_backend(engine)
    backend_url = f"{backend_base}/synthesize"

    log.info(
        "request received: segment_id=%s speaker=%s engine=%s backend=%s",
        request.segment_id, request.speaker, resolved_engine, backend_url,
    )

    # Forward the original request body as-is so no fields are dropped.
    body_bytes = request.model_dump_json().encode()

    start = time.monotonic()
    try:
        resp = await _http_client.post(
            backend_url,
            content=body_bytes,
            headers={"Content-Type": "application/json"},
        )
    except httpx.ConnectError as exc:
        log.error("backend unreachable: engine=%s url=%s error=%s", resolved_engine, backend_url, exc)
        raise HTTPException(
            status_code=503, detail=f"TTS backend unreachable: {backend_url} ({exc})"
        )
    except httpx.TimeoutException as exc:
        log.error("backend timeout: engine=%s url=%s error=%s", resolved_engine, backend_url, exc)
        raise HTTPException(
            status_code=504, detail=f"TTS backend timeout: {backend_url}"
        )
    duration_s = time.monotonic() - start

    log.info(
        "response sent: segment_id=%s engine=%s status=%s duration=%.2fs",
        request.segment_id, resolved_engine, resp.status_code, duration_s,
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
