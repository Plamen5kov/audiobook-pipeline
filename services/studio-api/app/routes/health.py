"""Health of this service, and of the pipeline it drives."""

import asyncio

import httpx
from fastapi import APIRouter, Request

router = APIRouter(tags=["health"])

_PIPELINE_SERVICES = {
    "text-analyzer":  "http://text-analyzer:8001/health",
    "xtts-v2":        "http://xtts-v2:8003/health",
    "tts-router":     "http://tts-router:8010/health",
    "qwen3-tts":      "http://qwen3-tts:8007/health",
    "audio-assembly": "http://audio-assembly:8005/health",
}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "studio-api"}


@router.get("/services/health")
async def services_health(request: Request):
    """Fan-out health check to all pipeline services in parallel."""
    client: httpx.AsyncClient = request.app.state.http_client

    async def check(name: str, url: str) -> dict:
        try:
            r = await client.get(url, timeout=3.0)
            data = r.json()
            return {"name": name, "status": data.get("status", "ok"), "detail": data}
        except Exception as exc:
            return {"name": name, "status": "error", "detail": str(exc)}

    results = await asyncio.gather(*[check(n, u) for n, u in _PIPELINE_SERVICES.items()])
    return list(results)
