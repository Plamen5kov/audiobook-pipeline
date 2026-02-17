import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel
from pydub import AudioSegment

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

    # Sort clips by ID to maintain reading order
    clips_sorted = sorted(request.clips, key=lambda c: c.id)

    combined = AudioSegment.empty()

    for clip in clips_sorted:
        if not os.path.exists(clip.file_path):
            raise HTTPException(status_code=400, detail=f"Audio file not found: {clip.file_path}")

        segment_audio = AudioSegment.from_file(clip.file_path)

        # Add pause before this segment
        if clip.pause_before_ms > 0:
            silence = AudioSegment.silent(duration=clip.pause_before_ms)
            combined += silence

        # Crossfade with previous audio if we have some
        if len(combined) > request.crossfade_ms and request.crossfade_ms > 0:
            combined = combined.append(segment_audio, crossfade=request.crossfade_ms)
        else:
            combined += segment_audio

    # Normalize volume
    if request.normalize:
        change_in_dbfs = request.target_dbfs - combined.dBFS
        combined = combined.apply_gain(change_in_dbfs)

    # Export
    output_filename = request.output_filename or f"chapter_{uuid.uuid4().hex[:8]}.wav"
    output_path = os.path.join(OUTPUT_DIR, output_filename)
    combined.export(output_path, format="wav")

    return {
        "file_path": output_path,
        "filename": output_filename,
        "duration_ms": len(combined),
        "clips_count": len(clips_sorted),
    }


@app.get("/health")
async def health():
    return {"status": "ok"}
