import logging
import os
import time

import soundfile as sf
import torch
import torchaudio

# ---------------------------------------------------------------------------
# torchaudio monkey-patch
# ---------------------------------------------------------------------------
# torchaudio 2.10 hardwires .load() to torchcodec which is not available on
# aarch64 (NVIDIA DGX Spark / GB10).  Replace it with a soundfile-based
# implementation that has identical return semantics (Tensor, int).


def _soundfile_load(uri, frame_offset=0, num_frames=-1, normalize=True,
                    channels_first=True, format=None, buffer_size=4096, backend=None):
    data, sr = sf.read(str(uri), start=frame_offset,
                       frames=num_frames if num_frames != -1 else -1,
                       dtype="float32", always_2d=True)
    tensor = torch.from_numpy(data.T if channels_first else data)
    return tensor, sr


torchaudio.load = _soundfile_load

from fastapi import FastAPI, HTTPException  # noqa: E402 -- must follow monkey-patch
from pydantic import BaseModel              # noqa: E402
from TTS.api import TTS                     # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="XTTS v2 TTS Service", description="Text-to-speech synthesis using Coqui XTTS v2")

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Model loads on startup.
tts_model: TTS | None = None

# Hardcoded voice cast used when the request does not supply reference_audio_path.
VOICE_CAST = {
    "narrator": "/voices/xtts/narrator.wav",
    "Elena":    "/voices/xtts/elena.wav",
    "Marcus":   "/voices/xtts/marcus.wav",
}
DEFAULT_VOICE = "/voices/xtts/generic_neutral.wav"


# ---------------------------------------------------------------------------
# Model lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def load_model():
    global tts_model
    log.info("loading model: model=xtts_v2")
    tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
    log.info("model loaded: model=xtts_v2")


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    text: str
    segment_id: int = 0                              # used in filename to preserve order
    speaker: str = "default"
    engine: str = "xtts-v2"                          # accepted for contract parity; not used here
    reference_audio_path: str = ""                   # optional override; resolved from VOICE_CAST if empty
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------


def _resolve_reference_audio(request: SynthesizeRequest) -> str:
    """Return the reference audio path, preferring the explicit override from the
    request, then the voice-cast lookup, then the default voice."""
    ref = request.reference_audio_path or VOICE_CAST.get(request.speaker, DEFAULT_VOICE)
    if not os.path.exists(ref):
        raise HTTPException(status_code=400, detail=f"Reference audio not found: {ref}")
    return ref


def _generate_audio(text: str, ref: str, speed: float, output_path: str) -> None:
    """Run XTTS v2 inference and write the result to *output_path*."""
    tts_model.tts_to_file(
        text=text,
        speaker_wav=ref,
        language="en",
        file_path=output_path,
        speed=speed,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    ref = _resolve_reference_audio(request)

    output_filename = f"seg{request.segment_id:04d}_{request.speaker}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    log.info(
        "request received: segment_id=%d speaker=%s ref=%s text=%.60s",
        request.segment_id, request.speaker, ref, request.text,
    )

    start = time.monotonic()
    try:
        _generate_audio(request.text, ref, request.speed, output_path)
    except Exception as exc:
        log.error("synthesis failed: segment_id=%d error=%s", request.segment_id, exc)
        raise HTTPException(status_code=500, detail=f"TTS generation failed: {exc}")
    duration_s = time.monotonic() - start

    log.info(
        "response sent: segment_id=%d speaker=%s file=%s duration=%.2fs",
        request.segment_id, request.speaker, output_path, duration_s,
    )
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
