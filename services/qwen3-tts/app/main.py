import logging
import os

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

# Maps pipeline emotion values → natural-language instruct phrases for Qwen.
EMOTION_PHRASES: dict[str, str] = {
    "neutral":       "",
    "happy":         "speak with warmth and cheerfulness",
    "sad":           "speak with a tone of sadness and melancholy",
    "angry":         "speak very angrily with sharp emphasis",
    "fearful":       "speak with a trembling, nervous, fearful tone",
    "excited":       "speak with high energy and excitement",
    "tense":         "speak with urgency and tension in your voice",
    "contemplative": "speak slowly and thoughtfully, as if pondering deeply",
}


def _load_voice_cast():
    global _voice_profiles
    if not os.path.exists(VOICE_CAST_PATH):
        log.warning("voice-cast.yaml not found at %s — using defaults for all speakers", VOICE_CAST_PATH)
        _voice_profiles = {}
        return
    with open(VOICE_CAST_PATH, "r") as f:
        config = yaml.safe_load(f)
    _voice_profiles = config.get("voices", {})
    log.info("Loaded %d voice profiles from %s", len(_voice_profiles), VOICE_CAST_PATH)


def _resolve_qwen_speaker(speaker: str) -> str:
    profile = _voice_profiles.get(speaker) or _voice_profiles.get("default") or {}
    qwen_speaker = profile.get("qwen_speaker")
    if not qwen_speaker:
        log.warning("No qwen_speaker set for speaker=%r — using default %r", speaker, QWEN_DEFAULT_SPEAKER)
        return QWEN_DEFAULT_SPEAKER
    return qwen_speaker


def _build_instruct(speaker: str, emotion: str) -> str | None:
    """Combine the per-character qwen_instruct baseline with the emotion phrase.
    Returns None when both are empty so the model uses its own default conditioning."""
    profile = _voice_profiles.get(speaker) or _voice_profiles.get("default") or {}
    base = profile.get("qwen_instruct", "").strip()
    emotion_phrase = EMOTION_PHRASES.get(emotion, "").strip()

    if base and emotion_phrase:
        return f"{base}, {emotion_phrase}"
    return base or emotion_phrase or None


@app.on_event("startup")
async def load_model():
    global tts_model
    _load_voice_cast()
    log.info("Loading Qwen3-TTS model: %s", MODEL_ID)
    tts_model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map="cuda:0",
        dtype=torch.bfloat16,
        # attn_implementation omitted — flash_attention_2 has no aarch64 wheel;
        # the model falls back to standard attention automatically.
    )
    log.info("Qwen3-TTS model loaded")


class SynthesizeRequest(BaseModel):
    text: str
    segment_id: int = 0
    speaker: str = "default"
    engine: str = "qwen3-tts"         # accepted for contract parity; not used here
    reference_audio_path: str = ""    # accepted but ignored — Qwen has no voice cloning
    qwen_speaker: str = ""            # override: if set, skip voice-cast.yaml lookup
    emotion: str = "neutral"
    intensity: float = 0.5
    speed: float = 1.0                # accepted but ignored — Qwen has no speed param


@app.post("/synthesize")
async def synthesize(request: SynthesizeRequest):
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    # Prefer explicit qwen_speaker from request (set by FE/n8n); fall back to voice-cast.yaml
    qwen_speaker = request.qwen_speaker.strip() if request.qwen_speaker.strip() else _resolve_qwen_speaker(request.speaker)
    instruct = _build_instruct(request.speaker, request.emotion)

    output_filename = f"seg{request.segment_id:04d}_{request.speaker}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    log.info(
        "Synthesizing segment %d speaker=%s qwen_speaker=%s emotion=%s instruct=%r text=%.60s",
        request.segment_id, request.speaker, qwen_speaker, request.emotion, instruct, request.text,
    )

    try:
        wavs, sr = tts_model.generate_custom_voice(
            text=request.text,
            language="English",
            speaker=qwen_speaker,
            instruct=instruct,
        )
        # generate_custom_voice returns a list of arrays; index 0 for single-text input
        sf.write(output_path, wavs[0], sr)
    except Exception as e:
        log.error("Qwen3-TTS generation failed for segment %d: %s", request.segment_id, e)
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
        "model": MODEL_ID,
    }
