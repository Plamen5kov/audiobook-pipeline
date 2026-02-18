import logging
import os

import numpy as np
import soundfile as sf
import torch
import torchaudio

# torchaudio 2.10 hardwires .load() to torchcodec which is not available on aarch64.
# Replace it with a soundfile-based implementation that has identical semantics.
def _soundfile_load(uri, frame_offset=0, num_frames=-1, normalize=True,
                    channels_first=True, format=None, buffer_size=4096, backend=None):
    data, sr = sf.read(str(uri), start=frame_offset,
                        frames=num_frames if num_frames != -1 else -1,
                        dtype="float32", always_2d=True)
    tensor = torch.from_numpy(data.T if channels_first else data)
    return tensor, sr

torchaudio.load = _soundfile_load

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from TTS.api import TTS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="XTTS v2 TTS Service", description="Text-to-speech synthesis using Coqui XTTS v2")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Model loads on startup
tts_model: TTS | None = None


@app.on_event("startup")
async def load_model():
    global tts_model
    log.info("Loading XTTS v2 model...")
    tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
    log.info("XTTS v2 model loaded")


VOICE_CAST = {
    "narrator": "/voices/narrator.wav",
    "Elena":    "/voices/elena.wav",
    "Marcus":   "/voices/marcus.wav",
}
DEFAULT_VOICE = "/voices/generic_neutral.wav"


class SynthesizeRequest(BaseModel):
    text: str
    segment_id: int = 0                              # used in filename to preserve order
    speaker: str = "default"
    reference_audio_path: str = ""                   # optional override; resolved from VOICE_CAST if empty
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    ref = request.reference_audio_path or VOICE_CAST.get(request.speaker, DEFAULT_VOICE)

    if not os.path.exists(ref):
        raise HTTPException(status_code=400, detail=f"Reference audio not found: {ref}")

    # Filename encodes segment order so audio-assembly can sort correctly
    output_filename = f"seg{request.segment_id:04d}_{request.speaker}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    log.info("Synthesizing segment %d speaker=%s ref=%s text=%.60s", request.segment_id, request.speaker, ref, request.text)
    try:
        tts_model.tts_to_file(
            text=request.text,
            speaker_wav=ref,
            language="en",
            file_path=output_path,
            speed=request.speed,
        )
    except Exception as e:
        log.error("TTS generation failed for segment %d: %s", request.segment_id, e)
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {e}")

    log.info("Segment %d saved to %s", request.segment_id, output_path)
    return {
        "segment_id": request.segment_id,
        "speaker": request.speaker,
        "file_path": output_path,
        "filename": output_filename,
    }


@app.get("/health")
async def health():
    return {
        "status": "ok" if tts_model is not None else "loading",
        "model": "xtts_v2",
    }
