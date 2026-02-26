import json
import logging
import os
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="Script Adapter", description="Rewrites text segments for optimal spoken delivery")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "llama3.1:70b")

PROMPTS_DIR = Path(__file__).parent / "prompts"
SYSTEM_PROMPT = (PROMPTS_DIR / "system.txt").read_text().strip()
USER_PROMPT_TEMPLATE = (PROMPTS_DIR / "user.txt").read_text().strip()


class Segment(BaseModel):
    id: int
    speaker: str
    original_text: str
    emotion: str = "neutral"
    intensity: float = 0.5
    pause_before_ms: int = 0


class AdaptRequest(BaseModel):
    segments: list[Segment]
    title: str = ""          # passed through from text-analyzer, ignored here
    characters: list[dict] = []  # passed through from text-analyzer, ignored here


class AdaptedSegment(BaseModel):
    id: int
    speaker: str
    original_text: str
    spoken_text: str
    emotion: str
    intensity: float
    pause_before_ms: int


class AdaptResponse(BaseModel):
    segments: list[AdaptedSegment]


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body_text = (await request.body()).decode(errors="replace")
    log.error("422 validation error on %s %s", request.method, request.url.path)
    log.error("Request body: %s", body_text[:2000])
    log.error("Validation errors: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors(), "body_preview": body_text[:500]},
    )


async def _call_ollama(prompt: str) -> str:
    """Call Ollama and return the raw response text."""
    log.info("Calling Ollama model=%s", MODEL_NAME)
    async with httpx.AsyncClient(timeout=None) as client:
        try:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "format": "json",
                    "options": {"num_predict": -1},
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Ollama request failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")

    raw_text = response.json().get("response", "")
    log.info("Ollama responded (%d chars)", len(raw_text))
    return raw_text


def _parse_adapted_segments(raw_text: str) -> dict[int, str]:
    """Parse LLM JSON response into {segment_id: spoken_text} lookup."""
    try:
        adapted_list = json.loads(raw_text)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", raw_text[:500])
        raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {raw_text[:500]}")

    if not isinstance(adapted_list, list):
        adapted_list = adapted_list.get("segments", []) if isinstance(adapted_list, dict) else []

    return {item["id"]: item["spoken_text"] for item in adapted_list if isinstance(item, dict)}


@app.post("/adapt", response_model=AdaptResponse)
async def adapt_script(request: AdaptRequest):
    log.info("POST /adapt — %d segments received", len(request.segments))

    segments_for_prompt = [
        {"id": s.id, "speaker": s.speaker, "original_text": s.original_text}
        for s in request.segments
    ]
    prompt = USER_PROMPT_TEMPLATE.format(segments_json=json.dumps(segments_for_prompt, indent=2))

    raw_text = await _call_ollama(prompt)
    spoken_lookup = _parse_adapted_segments(raw_text)
    log.info("spoken_lookup has %d entries", len(spoken_lookup))

    output_segments = [
        AdaptedSegment(
            id=seg.id,
            speaker=seg.speaker,
            original_text=seg.original_text,
            spoken_text=spoken_lookup.get(seg.id, seg.original_text),
            emotion=seg.emotion,
            intensity=seg.intensity,
            pause_before_ms=seg.pause_before_ms,
        )
        for seg in request.segments
    ]

    log.info("Returning %d adapted segments", len(output_segments))
    return AdaptResponse(segments=output_segments)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}
