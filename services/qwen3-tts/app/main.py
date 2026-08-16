import json
import logging
import os
import subprocess
import tempfile
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

import soundfile as sf
import torch
import yaml
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from qwen_tts import Qwen3TTSModel

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/data/intermediate")
VOICE_CAST_PATH = os.getenv("VOICE_CAST_PATH", "/voice-cast.yaml")
MODEL_ID = os.getenv("MODEL_ID", "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice")

os.makedirs(OUTPUT_DIR, exist_ok=True)

VOICEBANK_PATH = os.getenv("VOICEBANK_PATH", "/voicebank/voicebank.json")

tts_model: Qwen3TTSModel | None = None
_infer_lock = threading.Lock()
_voice_profiles: dict = {}
_voicebank: dict = {}
_clone_prompts: dict = {}

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


def _load_voicebank() -> None:
    """Load the cloning references exported from the aligned corpus.

    Each entry pairs a clip with the exact words spoken in it, which is what
    lets the model clone in in-context mode rather than from a speaker
    embedding alone.
    """
    global _voicebank
    manifest = Path(VOICEBANK_PATH)
    if not manifest.is_file():
        log.info("no voice bank at path=%s -- preset voices only", VOICEBANK_PATH)
        _voicebank = {}
        return
    data = json.loads(manifest.read_text())
    voices = data.get("voices", {})
    root = manifest.parent
    _voicebank = {}
    for name, clips in voices.items():
        # A clip may carry an emotion label. Grouping by it lets a line be
        # cloned from a reference the narrator delivered that way, which is the
        # only handle on delivery available: this checkpoint clones but takes
        # no emotion instruction, so tone has to come from the reference.
        by_emotion: dict[str, dict] = {}
        for clip in clips:
            path = root / clip["file"]
            if not path.is_file():
                continue
            emotion = (clip.get("emotion") or "neutral").lower()
            best = by_emotion.get(emotion)
            if best is None or (clip.get("align_score") or 0) > best["score"]:
                by_emotion[emotion] = {"audio": str(path),
                                       "text": clip["text"],
                                       "score": clip.get("align_score") or 0}
        if by_emotion:
            # Neutral is the fallback for any emotion with no clip of its own,
            # so a rare label degrades to a flat reading rather than failing.
            by_emotion.setdefault("neutral", next(iter(by_emotion.values())))
            _voicebank[name] = by_emotion
    log.info("voice bank loaded: %d voices from %s (%s)", len(_voicebank),
             manifest,
             ", ".join(f"{n}:{len(e)}" for n, e in list(_voicebank.items())[:6]))


def _clone_prompt(speaker: str, emotion: str = "neutral"):
    """Build (once) and return the clone prompt for *speaker*, or None.

    Cached per speaker *and* emotion: a chapter has thousands of segments but
    only a handful of voices, and building a prompt runs the model. Callers
    must hold the inference lock, since the model is not thread-safe.
    """
    key = f"{speaker}::{emotion}"
    if key in _clone_prompts:
        return _clone_prompts[key]
    voice = _voicebank.get(speaker)
    if not voice:
        _clone_prompts[key] = None
        return None
    entry = voice.get(emotion) or voice.get("neutral")
    if not entry:
        _clone_prompts[key] = None
        return None
    try:
        prompt = tts_model.create_voice_clone_prompt(
            ref_audio=entry["audio"],
            ref_text=entry["text"],
            x_vector_only_mode=False,
        )
        log.info("clone prompt built: speaker=%s emotion=%s ref=%s",
                 speaker, emotion, entry["audio"])
    except Exception as exc:
        log.error("clone prompt failed for speaker=%s emotion=%s: %s",
                  speaker, emotion, exc)
        prompt = None
    _clone_prompts[key] = prompt
    return prompt


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    global tts_model
    _load_voice_cast()
    log.info("loading model: model_id=%s", MODEL_ID)
    tts_model = Qwen3TTSModel.from_pretrained(
        MODEL_ID,
        device_map="cuda:0",
        dtype=torch.bfloat16,
    )
    log.info("model loaded: model_id=%s", MODEL_ID)
    _load_voicebank()
    yield


app = FastAPI(
    title="Qwen3 TTS Service",
    description="TTS synthesis using Qwen3-TTS-12Hz-1.7B-CustomVoice",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request schema
# ---------------------------------------------------------------------------


class SynthesizeRequest(BaseModel):
    text: str
    segment_id: int = 0
    speaker: str = "default"
    engine: str = "qwen3-tts"         # accepted for contract parity; not used here
    reference_audio_path: str = ""    # overrides the voice bank for this request
    reference_text: str = ""          # words spoken in the reference, for in-context cloning
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


def _generate_audio(text: str, qwen_speaker: str, instruct: str | None, output_path: str,
                    speed: float = 1.0, clone_prompt=None) -> None:
    """Run Qwen3-TTS inference and write the result to *output_path*.

    With a clone prompt the preset speaker and the emotion instruct are both
    unused: the reference clip defines the voice, and an instruct string cannot
    be passed to the clone path. That is a deliberate trade, since varying the
    instruct is what makes a correctly cast character stop sounding like
    himself across a chapter.
    """
    if clone_prompt is not None:
        wavs, sr = tts_model.generate_voice_clone(
            text=text,
            language="English",
            voice_clone_prompt=clone_prompt,
            non_streaming_mode=True,
        )
    else:
        wavs, sr = tts_model.generate_custom_voice(
            text=text,
            language="English",
            speaker=qwen_speaker,
            instruct=instruct,
        )
    # The generate_* calls return a list of arrays; index 0 for single-text input.
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
def synthesize(request: SynthesizeRequest):
    """Sync handler — FastAPI auto-offloads to threadpool, avoiding event loop blocking."""
    if tts_model is None:
        raise HTTPException(status_code=503, detail="Model not loaded yet")

    if not request.text or not request.text.strip():
        raise HTTPException(status_code=400, detail="text field is empty")

    qwen_speaker, instruct = _resolve_speaker_and_instruct(request)

    output_filename = f"seg{request.segment_id:04d}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)

    log.info(
        "request received: segment_id=%d speaker=%s qwen_speaker=%s emotion=%s instruct=%r text=%.60s",
        request.segment_id, request.speaker, qwen_speaker, request.emotion, instruct, request.text,
    )

    start = time.monotonic()
    with _infer_lock:
        try:
            if request.reference_audio_path:
                # Cached on the reference itself, so comparing several
                # candidate voices does not rebuild the same prompt per line.
                key = f"ref::{request.reference_audio_path}"
                if key not in _clone_prompts:
                    _clone_prompts[key] = tts_model.create_voice_clone_prompt(
                        ref_audio=request.reference_audio_path,
                        ref_text=request.reference_text or None,
                        x_vector_only_mode=not request.reference_text,
                    )
                    log.info("clone prompt built from request ref=%s",
                             request.reference_audio_path)
                clone_prompt = _clone_prompts[key]
            else:
                clone_prompt = _clone_prompt(request.speaker,
                                             (request.emotion or "neutral").lower())
            if clone_prompt is not None and request.qwen_speaker.strip():
                # A caller asked for a named voice and is getting a cloned one,
                # because this checkpoint has no presets and the voice bank has
                # a clip for this speaker. Silently substituting is how a cast
                # choice disappears without a trace; route to the CustomVoice
                # deployment (engine "qwen3-preset") to actually get the preset.
                log.warning(
                    "preset %r ignored: %s has no preset speakers, cloning %r "
                    "from the voice bank instead. Use engine 'qwen3-preset' "
                    "for named voices.",
                    request.qwen_speaker.strip(), MODEL_ID, request.speaker)
            _generate_audio(request.text, qwen_speaker, instruct, output_path,
                            request.speed, clone_prompt)
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
        "service": "qwen3-tts",
        "model": MODEL_ID,
    }
