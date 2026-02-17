import json
import os

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Text Analyzer", description="Parses chapter text into structured segments with speaker/emotion metadata")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "llama3.1:70b")

SYSTEM_PROMPT = """You are a text analysis engine for audiobook production. Given a chapter of text, you must:

1. Identify all characters who speak in the text.
2. Break the text into ordered segments — each segment is either narration or a single character's dialogue.
3. For each segment, detect the emotional tone and intensity.

Return ONLY valid JSON matching this schema (no markdown, no explanation):

{
  "characters": [
    {"name": "narrator", "description": "..."},
    {"name": "CharacterName", "description": "brief description based on text clues"}
  ],
  "segments": [
    {
      "id": 1,
      "speaker": "narrator" or "CharacterName",
      "original_text": "exact text from the source",
      "emotion": "neutral|happy|sad|angry|fearful|excited|tense|contemplative",
      "intensity": 0.0 to 1.0,
      "pause_before_ms": milliseconds of pause before this segment (0 for first)
    }
  ]
}

Rules:
- Always include "narrator" in characters.
- Keep segments in reading order.
- Dialogue includes the quoted text AND any attribution ("he said") as original_text — the script adapter will clean it later.
- Narration segments can span multiple sentences but should break at natural paragraph or scene boundaries.
- pause_before_ms: use 0 for continuous flow, 300 for dialogue turns, 800 for scene/paragraph breaks."""


class AnalyzeRequest(BaseModel):
    text: str
    title: str = "Untitled Chapter"


class AnalyzeResponse(BaseModel):
    title: str
    characters: list[dict]
    segments: list[dict]


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_text(request: AnalyzeRequest):
    prompt = f"Analyze this chapter text:\n\n{request.text}"

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
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {raw_text[:500]}")

    characters = [c for c in parsed.get("characters", []) if isinstance(c, dict)]
    segments = [s for s in parsed.get("segments", []) if isinstance(s, dict)]

    return AnalyzeResponse(
        title=request.title,
        characters=characters,
        segments=segments,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}
