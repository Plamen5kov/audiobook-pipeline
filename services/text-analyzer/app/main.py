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

app = FastAPI(title="Text Analyzer", description="Parses chapter text into structured segments with speaker/emotion metadata")

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "llama3.1:70b")

SYSTEM_PROMPT = """You are a text analysis engine for audiobook production. Your job is to analyze the EXACT TEXT provided by the user and break it into segments for narration and dialogue.

CRITICAL RULES — you must follow these without exception:
1. NEVER invent, paraphrase, or hallucinate any text. Every "original_text" value must be copied VERBATIM from the user's input.
2. NEVER produce an empty "original_text". If a segment has no text, do not include that segment.
3. A character segment contains ONLY the words inside the quotation marks — nothing else. Strip the surrounding quotation marks from original_text. A character's original_text must NEVER start or end with a quotation mark (").
4. ABSOLUTE RULE — Any text surrounded by quotation marks is ALWAYS dialogue spoken by a character. It must NEVER have speaker="narrator". If you cannot identify the speaker from context, assign it to the most recently mentioned character.
5. Attributions and all text outside quotes belong to the narrator, even when they appear mid-sentence between two quoted parts.
6. When a sentence contains split dialogue — two quoted parts with an attribution in the middle (e.g. "Quote one," she said. "Quote two.") — produce THREE segments in order: character ("Quote one,"), narrator ("she said."), character ("Quote two.").
7. Everything else — narration, description, action — belongs to the narrator speaker. Narrator original_text must NEVER be surrounded by quotation marks.
8. Keep all segments in reading order. Do not skip any text from the source.

Return ONLY valid JSON matching this schema exactly (no markdown, no explanation, no extra keys):

{
  "characters": [
    {"name": "narrator", "description": "the narrative voice"},
    {"name": "CharacterName", "description": "brief description based on text clues"}
  ],
  "segments": [
    {
      "id": 1,
      "speaker": "narrator",
      "original_text": "verbatim text copied from the source",
      "emotion": "neutral",
      "intensity": 0.5,
      "pause_before_ms": 0
    }
  ]
}

Schema rules:
- "speaker": use "narrator" for narration/description, or the character's exact name for quoted dialogue.
- "emotion": one of neutral, happy, sad, angry, fearful, excited, tense, contemplative.
- "intensity": 0.0 to 1.0.
- "pause_before_ms": 0 for first segment and continuous flow, 300 for dialogue turns, 800 for paragraph/scene breaks.
- Always include "narrator" in the characters list."""


class AnalyzeRequest(BaseModel):
    text: str
    title: str = "Untitled Chapter"


class AnalyzeResponse(BaseModel):
    title: str
    characters: list[dict]
    segments: list[dict]


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    log.error("422 validation error on %s %s", request.method, request.url.path)
    log.error("Request body: %s", body.decode(errors="replace")[:2000])
    log.error("Validation errors: %s", exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors(), "body_preview": body.decode(errors="replace")[:500]})


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_text(request: AnalyzeRequest):
    log.info("POST /analyze — title=%r text_length=%d", request.title, len(request.text))
    log.info("Text preview: %.200s", request.text)

    prompt = f"Analyze this chapter text:\n\n{request.text}"

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

    result = response.json()
    raw_text = result.get("response", "")
    log.info("Ollama responded (%d chars)", len(raw_text))

    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        log.error("LLM returned invalid JSON: %s", raw_text[:500])
        raise HTTPException(status_code=500, detail=f"LLM returned invalid JSON: {raw_text[:500]}")

    characters = [c for c in parsed.get("characters", []) if isinstance(c, dict)]
    segments = [s for s in parsed.get("segments", []) if isinstance(s, dict)]

    # Post-processing guard: fix obvious attribution errors from smaller models.
    # Rule: a segment whose original_text is entirely surrounded by quotes can never
    # belong to the narrator — it is dialogue. Also strip surrounding quotes so TTS
    # doesn't read them aloud.
    known_chars = [c["name"] for c in characters if c.get("name") and c["name"] != "narrator"]
    last_char = known_chars[0] if known_chars else "unknown"
    for seg in segments:
        text = seg.get("original_text", "")
        is_quoted = (text.startswith('"') and text.endswith('"')) or \
                    (text.startswith('\u201c') and text.endswith('\u201d'))
        if is_quoted:
            # Strip surrounding quotes
            seg["original_text"] = text[1:-1].strip()
            if seg.get("speaker") == "narrator":
                seg["speaker"] = last_char
                log.warning("Fixed misattributed dialogue (was narrator): %s", seg["original_text"][:60])
        elif seg.get("speaker") != "narrator":
            last_char = seg["speaker"]

    log.info("Parsed %d characters, %d segments", len(characters), len(segments))

    return AnalyzeResponse(
        title=request.title,
        characters=characters,
        segments=segments,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}
