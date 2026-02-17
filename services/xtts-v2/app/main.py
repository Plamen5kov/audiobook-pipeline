import io
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from TTS.api import TTS

app = FastAPI(title="XTTS v2 TTS Service", description="Text-to-speech synthesis using Coqui XTTS v2")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Model loads on startup
tts_model: TTS | None = None


@app.on_event("startup")
async def load_model():
    global tts_model
    tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)


class SynthesizeRequest(BaseModel):
    text: str
    speaker_id: str = "default"
    reference_audio_path: str = "/voices/generic_neutral.wav"
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not os.path.exists(request.reference_audio_path):
        raise HTTPException(status_code=400, detail=f"Reference audio not found: {request.reference_audio_path}")

    output_filename = f"{request.speaker_id}_{uuid.uuid4().hex[:8]}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    try:
        tts_model.tts_to_file(
            text=request.text,
            speaker_wav=request.reference_audio_path,
            language="en",
            file_path=output_path,
            speed=request.speed,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")

    return FileResponse(
        output_path,
        media_type="audio/wav",
        filename=output_filename,
    )


@app.get("/health")
async def health():
    return {
        "status": "ok" if tts_model is not None else "loading",
        "model": "xtts_v2",
    }
