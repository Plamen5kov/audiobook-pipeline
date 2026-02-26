import logging
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pydub import AudioSegment

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
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


@app.post("/assemble")
async def assemble(request: AssembleRequest):
    if not request.clips:
        raise HTTPException(status_code=400, detail="No clips provided")

    clips_sorted = sorted(request.clips, key=lambda c: c.id)
    log.info("POST /assemble — %d clips, crossfade=%dms, normalize=%s",
             len(clips_sorted), request.crossfade_ms, request.normalize)

    combined = AudioSegment.empty()

    for clip in clips_sorted:
        if not os.path.exists(clip.file_path):
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

    if request.normalize:
        change_in_dbfs = request.target_dbfs - combined.dBFS
        combined = combined.apply_gain(change_in_dbfs)
        log.info("Normalized: %.1f dBFS → %.1f dBFS (gain %.1f dB)",
                 combined.dBFS - change_in_dbfs, combined.dBFS, change_in_dbfs)

    output_filename = request.output_filename or f"chapter_{uuid.uuid4().hex[:8]}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    combined.export(output_path, format="wav")

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
    return {"status": "ok"}
