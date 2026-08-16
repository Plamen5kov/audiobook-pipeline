import glob
import logging
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pydub import AudioSegment

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger(__name__)

app = FastAPI(title="Audio Assembly", description="Combines audio segments into a complete audiobook chapter")

INTERMEDIATE_DIR = os.getenv("INTERMEDIATE_DIR", "/data/intermediate")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
os.makedirs(OUTPUT_DIR, exist_ok=True)


class AudioClip(BaseModel):
    id: int
    file_path: str
    pause_before_ms: int = 0


class AssembleRequest(BaseModel):
    clips: list[AudioClip]
    output_filename: str = ""
    crossfade_ms: int = 50
    normalize: bool = True
    target_dbfs: float = -20.0
    # Audiobook distribution format. 64k mono is the usual spoken-word bitrate
    # and turns a 25-minute chapter from ~250MB of WAV into ~12MB.
    output_format: str = "mp3"
    mp3_bitrate: str = "64k"
    mono: bool = True


def _find_latest_segment_file(original_path: str, segment_id: int) -> str | None:
    """Find the most recently modified wav for a given segment ID when the
    expected file is missing (e.g. speaker name changed after re-synthesis)."""
    directory = os.path.dirname(original_path) or INTERMEDIATE_DIR
    pattern = os.path.join(directory, f"seg{segment_id:04d}_*.wav")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    # Pick the most recently modified file.
    return max(candidates, key=os.path.getmtime)


@app.post("/assemble")
def assemble(request: AssembleRequest):
    if not request.clips:
        raise HTTPException(status_code=400, detail="No clips provided")

    clips_sorted = sorted(request.clips, key=lambda c: c.id)
    log.info("POST /assemble — %d clips, crossfade=%dms, normalize=%s",
             len(clips_sorted), request.crossfade_ms, request.normalize)

    combined = AudioSegment.empty()

    for clip in clips_sorted:
        if not os.path.exists(clip.file_path):
            # The clip path may be stale if the speaker changed between analysis
            # and synthesis.  Find the most recent file for this segment ID.
            resolved = _find_latest_segment_file(clip.file_path, clip.id)
            if resolved:
                log.warning("  clip %d: %s not found, using %s", clip.id, clip.file_path, resolved)
                clip.file_path = resolved
            else:
                raise HTTPException(status_code=400, detail=f"Audio file not found: {clip.file_path}")

        segment_audio = AudioSegment.from_file(clip.file_path)
        log.info("  clip %d: %s (pause=%dms, dur=%dms)",
                 clip.id, clip.file_path, clip.pause_before_ms, len(segment_audio))

        if clip.pause_before_ms > 0:
            combined += AudioSegment.silent(duration=clip.pause_before_ms)
            combined += segment_audio
        elif len(combined) > request.crossfade_ms and request.crossfade_ms > 0:
            combined = combined.append(segment_audio, crossfade=request.crossfade_ms)
        else:
            combined += segment_audio

    if request.normalize and combined.dBFS > -80:
        change_in_dbfs = request.target_dbfs - combined.dBFS
        combined = combined.apply_gain(change_in_dbfs)
        log.info("Normalized: %.1f dBFS → %.1f dBFS (gain %.1f dB)",
                 combined.dBFS - change_in_dbfs, combined.dBFS, change_in_dbfs)
    elif request.normalize:
        log.warning("Skipping normalization — audio is silent (dBFS=%.1f)", combined.dBFS)

    fmt = (request.output_format or "mp3").lower()

    if request.mono and combined.channels > 1:
        combined = combined.set_channels(1)

    output_filename = request.output_filename or f"chapter_{uuid.uuid4().hex[:8]}.{fmt}"
    # Honour the requested format even if the caller passed a stale extension.
    stem, _, ext = output_filename.rpartition(".")
    if stem and ext.lower() != fmt:
        output_filename = f"{stem}.{fmt}"

    output_path = os.path.join(OUTPUT_DIR, output_filename)

    export_kwargs: dict = {"format": fmt}
    if fmt == "mp3":
        export_kwargs["bitrate"] = request.mp3_bitrate
        export_kwargs["parameters"] = ["-q:a", "2"]
    combined.export(output_path, **export_kwargs)

    duration_s = len(combined) / 1000
    log.info("Exported: %s (%.1fs, %d clips)", output_path, duration_s, len(clips_sorted))

    return {
        "file_path": output_path,
        "filename": output_filename,
        "duration_ms": len(combined),
        "clips_count": len(clips_sorted),
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "audio-assembly"}
