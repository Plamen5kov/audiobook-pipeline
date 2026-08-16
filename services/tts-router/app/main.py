import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

# Backend map: engine name -> base URL, or a list of URLs for replicas.
# Example: '{"xtts-v2":"http://xtts-v2:8003",
#            "qwen3-tts":["http://qwen3-tts:8007","http://qwen3-tts-2:8007"]}'
def _load_backends() -> dict[str, list[str]]:
    raw = json.loads(os.getenv("TTS_BACKENDS", "{}"))
    return {engine: ([urls] if isinstance(urls, str) else list(urls))
            for engine, urls in raw.items()}


BACKENDS: dict[str, list[str]] = _load_backends()
DEFAULT_ENGINE: str = os.getenv("DEFAULT_ENGINE", "xtts-v2")

# Reuse a single async HTTP client across requests to benefit from connection pooling.
_http_client: httpx.AsyncClient | None = None

# One queue of free replicas per engine. Each backend serialises generation
# internally, so handing a request a specific idle replica is what actually
# parallelises the work — raising client concurrency against one replica only
# lengthens its queue.
_pools: dict[str, asyncio.Queue] = {}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _http_client
    _http_client = httpx.AsyncClient(timeout=1200.0)

    if not BACKENDS:
        log.warning("TTS_BACKENDS env var is empty -- no backends configured")
    else:
        log.info("TTS backends loaded:")
        for engine, urls in BACKENDS.items():
            queue: asyncio.Queue = asyncio.Queue()
            for url in urls:
                queue.put_nowait(url)
            _pools[engine] = queue
            log.info("  %s -> %d replica(s): %s", engine, len(urls), ", ".join(urls))
    log.info("default_engine=%s", DEFAULT_ENGINE)

    yield

    if _http_client:
        await _http_client.aclose()


app = FastAPI(
    title="TTS Router",
    description="Routes /synthesize requests to the correct TTS backend based on the 'engine' field.",
    lifespan=lifespan,
)


def _resolve_engine(engine: str) -> str:
    """Return the engine to use, falling back to the default if unregistered."""
    if engine in _pools:
        return engine
    if DEFAULT_ENGINE not in _pools:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown engine {engine!r} and no default backend configured",
        )
    log.warning("engine=%r not found, falling back to engine=%r", engine, DEFAULT_ENGINE)
    return DEFAULT_ENGINE


@app.post("/synthesize")
async def synthesize(request: Request):
    # Read raw body and parse only what we need for routing.
    body_bytes = await request.body()
    try:
        body_json = json.loads(body_bytes)
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    engine = (body_json.get("engine") or "").strip() or DEFAULT_ENGINE
    segment_id = body_json.get("segment_id", "?")
    speaker = body_json.get("speaker", "?")

    resolved_engine = _resolve_engine(engine)
    pool = _pools[resolved_engine]

    # Wait for a free replica rather than piling onto a busy one. With a single
    # replica this is exactly the old behaviour.
    queued_at = time.monotonic()
    backend_base = await pool.get()
    waited = time.monotonic() - queued_at
    backend_url = f"{backend_base}/synthesize"

    log.info(
        "request received: segment_id=%s speaker=%s engine=%s backend=%s waited=%.1fs",
        segment_id, speaker, resolved_engine, backend_url, waited,
    )

    # Forward the original body as-is so no fields are dropped.
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
    finally:
        # The replica must go back in the pool even when the call failed, or a
        # transient backend error would permanently shrink capacity.
        pool.put_nowait(backend_base)
    duration_s = time.monotonic() - start

    log.info(
        "response sent: segment_id=%s engine=%s status=%s duration=%.2fs",
        segment_id, resolved_engine, resp.status_code, duration_s,
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
        "service": "tts-router",
        "default_engine": DEFAULT_ENGINE,
        "backends": BACKENDS,
        "replicas": {e: len(u) for e, u in BACKENDS.items()},
        "idle": {e: q.qsize() for e, q in _pools.items()},
    }
