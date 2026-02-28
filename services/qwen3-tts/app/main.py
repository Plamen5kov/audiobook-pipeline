import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

import soundfile as sf
import torch
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qwen_tts import Qwen3TTSModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(
    title="Qwen3 TTS Service",
    description="TTS synthesis using Qwen3-TTS-12Hz-1.7B-CustomVoice",
)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
VOICE_CAST_PATH = os.getenv("VOICE_CAST_PATH", "/voice-cast.yaml")
MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")

os.makedirs(OUTPUT_DIR, exist_ok=True)

tts_model: Qwen3TTSModel | None = None
_voice_profiles: dict = {}

# Fallback Qwen speaker when voice-cast.yaml has no qwen_speaker for a character.
QWEN_DEFAULT_SPEAKER = "Ryan"

# ---------------------------------------------------------------------------
# Emotion phrase mapping
# ---------------------------------------------------------------------------
# Maps pipeline emotion values to natural-language instruct phrases for Qwen.
# Loaded from prompts/emotion_phrases.txt (one "emotion=phrase" per line).
PROMPTS_DIR = Path(__file__).parent / "prompts"


def _load_emotion_phrases() -> dict[str, str]:
    """Parse the emotion_phrases.txt file into an {emotion: phrase} dict."""
    phrases: dict[str, str] = {}
    for line in (PROMPTS_DIR / "emotion_phrases.txt").read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        phrases[key.strip()] = value.strip()
    return phrases


EMOTION_PHRASES: dict[str, str] = _load_emotion_phrases()

# ---------------------------------------------------------------------------
# Voice cast helpers
# ---------------------------------------------------------------------------


def _load_voice_cast() -> None:
    """Load voice profiles from voice-cast.yaml into the module-level dict."""
    global _voice_profiles
    if not os.path.exists(VOICE_CAST_PATH):
        log.warning("voice-cast.yaml not found at path=%s -- using defaults for all speakers", VOICE_CAST_PATH)
        _voice_profiles = {}
        return
    with open(VOICE_CAST_PATH, "r") as f:
        config = yaml.safe_load(f)
    _voice_profiles = config.get("voices", {})
    log.info("voice cast loaded: profiles=%d path=%s", len(_voice_profiles), VOICE_CAST_PATH)


def _resolve_qwen_speaker(speaker: str) -> str:
    """Look up the qwen_speaker for *speaker* in the voice cast, falling back to the default."""
    profile = _voice_profiles.get(speaker) or _voice_profiles.get("default") or {}
    qwen_speaker = profile.get("qwen_speaker")
    if not qwen_speaker:
        log.warning("no qwen_speaker for speaker=%s -- using default=%s", speaker, QWEN_DEFAULT_SPEAKER)
        return QWEN_DEFAULT_SPEAKER
    return qwen_speaker


def _build_instruct(speaker: str, emotion: str) -> str | None:
    """Combine the per-character qwen_instruct baseline with the emotion phrase.

    Returns None when both are empty so the model uses its own default conditioning.
    """
    profile = _voice_profiles.get(speaker) or _voice_profiles.get("default") or {}
    base = profile.get("qwen_instruct", "").strip()
    emotion_phrase = EMOTION_PHRASES.get(emotion, "").strip()

    if base and emotion_phrase:
        return f"{base}, {emotion_phrase}"
    return base or emotion_phrase or None


# ---------------------------------------------------------------------------
# Model lifecycle
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def load_model():
    global tts_model
    _load_voice_cast()
    log.info("loading model: model_id=%s", MODEL_ID)
    tts_model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        # attn_implementation omitted -- flash_attention_2 has no aarch64 wheel;
        # the model falls back to standard attention automatically.
    )
    log.info("model loaded: model_id=%s", MODEL_ID)


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    text: str
    segment_id: int = 0
    speaker: str = "default"
    engine: str = "qwen3-tts"         # accepted for contract parity; not used here
    reference_audio_path: str = ""    # accepted but ignored -- Qwen has no voice cloning
    qwen_speaker: str = ""            # override: if set, skip voice-cast.yaml lookup
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0                # applied as post-processing resampling


# ---------------------------------------------------------------------------
# Synthesis helpers
# ---------------------------------------------------------------------------


def _resolve_speaker_and_instruct(request: SynthesizeRequest) -> tuple[str, str | None]:
    """Determine the Qwen speaker name and instruct string from the request."""
    explicit = request.qwen_speaker.strip()
    qwen_speaker = explicit if explicit else _resolve_qwen_speaker(request.speaker)
    instruct = _build_instruct(request.speaker, request.emotion)
    return qwen_speaker, instruct


def _generate_audio(text: str, qwen_speaker: str, instruct: str | None, output_path: str, speed: float = 1.0) -> None:
    """Run Qwen3-TTS inference and write the result to *output_path*."""
    wavs, sr = tts_model.generate_custom_voice(
        text=text,
        language="English",
        speaker=qwen_speaker,
        instruct=instruct,
    )
    # generate_custom_voice returns a list of arrays; index 0 for single-text input.
    audio = wavs[0]
    sf.write(output_path, audio, sr)

    # Apply tempo change via ffmpeg atempo filter (preserves pitch).
    if speed != 1.0 and 0.25 <= speed <= 4.0:
        _apply_atempo(output_path, speed)


def _apply_atempo(file_path: str, speed: float) -> None:
    """Use ffmpeg atempo filter to change tempo without pitch shift.

    ffmpeg's atempo accepts values in [0.5, 100.0]. For speeds below 0.5 we
    chain multiple atempo filters (each >=0.5).
    """
    # Build atempo filter chain — each filter limited to [0.5, 100.0].
    filters: list[str] = []
    remaining = speed
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining}")
    af = ",".join(filters)

    with tempfile.NamedTemporaryFile(suffix=".wav", dir=os.path.dirname(file_path), delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", file_path, "-filter:a", af, tmp_path],
            check=True,
            capture_output=True,
        )
        os.replace(tmp_path, file_path)
    except subprocess.CalledProcessError as exc:
        log.error("ffmpeg atempo failed: %s", exc.stderr.decode(errors="replace"))
        # Clean up temp file; original file is untouched.
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    qwen_speaker, instruct = _resolve_speaker_and_instruct(request)

    output_filename = f"seg{request.segment_id:04d}_{request.speaker}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    log.info(
        "request received: segment_id=%d speaker=%s qwen_speaker=%s emotion=%s instruct=%r text=%.60s",
        request.segment_id, request.speaker, qwen_speaker, request.emotion, instruct, request.text,
    )

    start = time.monotonic()
    try:
        _generate_audio(request.text, qwen_speaker, instruct, output_path, request.speed)
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
        "model": MODEL_ID,
    }
