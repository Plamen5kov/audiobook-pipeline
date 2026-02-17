import json
import logging
import os

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

SYSTEM_PROMPT = """You are a script adapter for audiobook production. You receive structured text segments and must rewrite them for spoken delivery.

For each segment, produce a "spoken_text" version by applying these rules:

1. For DIALOGUE segments: strip attribution phrases ("she said", "he whispered angrily", etc.) — keep only the words the character actually says. The emotion and delivery style will be handled by the TTS engine.
2. For NARRATION segments: keep the full text but optimize for listening:
   - Expand abbreviations ("Dr." → "Doctor", "St." → "Street" or "Saint" based on context)
   - Convert numbers to words ("42" → "forty-two", "1984" → "nineteen eighty-four" when it's a year)
   - Expand acronyms if they would be spoken as words
3. Do NOT change the meaning, tone, or content — only adapt the form for speech.

Return ONLY valid JSON — an array of objects with "id" and "spoken_text" fields:
[
  {"id": 1, "spoken_text": "adapted text here"},
  {"id": 2, "spoken_text": "adapted text here"}
]"""


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
    body = await request.body()
    log.error("422 validation error on %s %s", request.method, request.url.path)
    log.error("Request body: %s", body.decode(errors="replace")[:2000])
    log.error("Validation errors: %s", exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body_preview": body.decode(errors="replace")[:500]})


@app.post("/adapt", response_model=AdaptResponse)
async def adapt_script(request: AdaptRequest):
    log.info("POST /adapt — %d segments received", len(request.segments))
    for seg in request.segments:
        log.info("  segment %d: speaker=%s text=%.60s", seg.id, seg.speaker, seg.original_text)

    segments_for_prompt = [
        {"id": s.id, "speaker": s.speaker, "original_text": s.original_text}
        for s in request.segments
    ]
    prompt = f"Adapt these segments for spoken delivery:\n\n{json.dumps(segments_for_prompt, indent=2)}"

    log.info("Calling Ollama model=%s", MODEL_NAME)
    async with httpx.AsyncClient(timeout=300.0) as client:
        try:
            response = await client.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": MODEL_NAME,
                    "prompt": prompt,
                    "system": SYSTEM_PROMPT,
                    "stream": False,
                    "format": "json",
                },
            )
            response.raise_for_status()
        except httpx.HTTPError as e:
            log.error("Ollama request failed: %s", e)
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")

    result = response.json()
    raw_text = result.get("response", "")
    log.info("Ollama responded (%d chars)", len(raw_text))

    try:
        adapted_list = json.loads(raw_text)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", raw_text[:500])
        raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {raw_text[:500]}")

    if not isinstance(adapted_list, list):
        adapted_list = adapted_list.get("segments", []) if isinstance(adapted_list, dict) else []

    # Build lookup from LLM response
    spoken_lookup = {item["id"]: item["spoken_text"] for item in adapted_list if isinstance(item, dict)}
    log.info("spoken_lookup has %d entries", len(spoken_lookup))

    # Merge spoken_text back into original segments
    output_segments = []
    for seg in request.segments:
        output_segments.append(AdaptedSegment(
            id=seg.id,
            speaker=seg.speaker,
            original_text=seg.original_text,
            spoken_text=spoken_lookup.get(seg.id, seg.original_text),
            emotion=seg.emotion,
            intensity=seg.intensity,
            pause_before_ms=seg.pause_before_ms,
        ))

    log.info("Returning %d adapted segments", len(output_segments))
    return AdaptResponse(segments=output_segments)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}
