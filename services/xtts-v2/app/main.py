import logging
import os
import time
from contextlib import asynccontextmanager

import soundfile as sf
import torch
import torchaudio
import yaml

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

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Model loads on startup.
tts_model: TTS | None = None

VOICE_CAST_PATH = os.getenv("VOICE_CAST_PATH", "/voice-cast.yaml")
DEFAULT_VOICE = "/voices/xtts/generic_neutral.wav"

# Voice cast loaded from voice-cast.yaml at startup.
VOICE_CAST: dict[str, str] = {}


def _load_voice_cast() -> None:
    """Load voice-cast.yaml and build speaker→reference_audio mapping."""
    global VOICE_CAST
    if not os.path.exists(VOICE_CAST_PATH):
        log.warning("voice-cast.yaml not found at %s — using defaults", VOICE_CAST_PATH)
        return
    with open(VOICE_CAST_PATH) as f:
        config = yaml.safe_load(f)
    voices = config.get("voices", {})
    VOICE_CAST = {
        name: profile.get("reference_audio", DEFAULT_VOICE)
        for name, profile in voices.items()
        if profile.get("reference_audio")
    }
    log.info("voice cast loaded: %d entries from %s", len(VOICE_CAST), VOICE_CAST_PATH)


# ---------------------------------------------------------------------------
# Model lifecycle
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_model
    _load_voice_cast()
    log.info("loading model: model=xtts_v2")
    tts_model = TTS("tts_models/multilingual/multi-dataset/xtts_v2", gpu=True)
    log.info("model loaded: model=xtts_v2")
    yield


app = FastAPI(
    title="XTTS v2 TTS Service",
    description="Text-to-speech synthesis using Coqui XTTS v2",
    lifespan=lifespan,
)


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
def synthesize(request: SynthesizeRequest):
    """Sync handler — FastAPI auto-offloads to threadpool, avoiding event loop blocking."""
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
        "service": "xtts-v2",
        "model": "xtts_v2",
    }
