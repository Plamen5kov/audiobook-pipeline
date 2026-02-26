import logging
import os

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .pipeline import run_pipeline

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="Text Analyzer",
    description="Hybrid pipeline for audiobook text analysis — "
                "deterministic parsing + targeted AI",
)

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
MODEL_NAME = os.getenv("MODEL_NAME", "qwen2.5:7b")


class AnalyzeRequest(BaseModel):
    text: str
    title: str = "Untitled Chapter"


class AnalyzeResponse(BaseModel):
    title: str
    characters: list[dict]
    segments: list[dict]
    report: dict = {}  # per-node timing breakdown (backward-compatible)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    body = await request.body()
    log.error("422 validation error on %s %s", request.method, request.url.path)
    log.error("Request body: %s", body.decode(errors="replace")[:2000])
    log.error("Validation errors: %s", exc.errors())
    return JSONResponse(
        status_code=422,
        content={
            "detail": exc.errors(),
            "body_preview": body.decode(errors="replace")[:500],
        },
    )


@app.post("/analyze", response_model=AnalyzeResponse)
async def analyze_text(request: AnalyzeRequest):
    log.info("POST /analyze — title=%r text_length=%d", request.title, len(request.text))
    log.info("Text preview: %.200s", request.text)

    result = await run_pipeline(
        text=request.text,
        title=request.title,
        ollama_url=OLLAMA_BASE_URL,
        model_name=MODEL_NAME,
    )

    return AnalyzeResponse(
        title=result.title,
        characters=result.characters,
        segments=result.segments,
        report=result.report,
    )


@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME, "pipeline": "hybrid"}
