import json
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

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


@app.post("/adapt", response_model=AdaptResponse)
async def adapt_script(request: AdaptRequest):
    segments_for_prompt = [
        {"id": s.id, "speaker": s.speaker, "original_text": s.original_text}
        for s in request.segments
    ]
    prompt = f"Adapt these segments for spoken delivery:\n\n{json.dumps(segments_for_prompt, indent=2)}"

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
            raise HTTPException(status_code=502, detail=f"Ollama request failed: {e}")

    result = response.json()
    raw_text = result.get("response", "")

    try:
        adapted_list = json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {raw_text[:500]}")

    # Build lookup from LLM response
    spoken_lookup = {item["id"]: item["spoken_text"] for item in adapted_list}

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

    return AdaptResponse(segments=output_segments)


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}
