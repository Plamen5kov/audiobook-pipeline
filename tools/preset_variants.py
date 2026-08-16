"""Render one character's lines with each of the engine's preset voices.

Cloning and preset voices live in different checkpoints, so this targets the
preset backend explicitly rather than the cloning pool. Useful for a character
who should sound like nobody in the corpus.

Usage: preset_variants.py <out_dir> <analysis.json> <speaker> <voice,voice,...>
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

ROUTER = "http://tts-router:8010/synthesize"
ASSEMBLY = "http://audio-assembly:8005/assemble"
ENGINE = "qwen3-preset"
BASE_BLOCK = 70000


def text_of(seg: dict) -> str:
    return (seg.get("original_text") or seg.get("text") or "").strip()


async def _synth(client, sem, seg, uid, voice):
    async with sem:
        try:
            r = await client.post(ROUTER, json={
                "text": text_of(seg),
                "segment_id": uid,
                "speaker": seg.get("speaker", "narrator"),
                "engine": ENGINE,
                "qwen_speaker": voice,
                "emotion": seg.get("emotion", "neutral"),
            })
            if r.status_code == 200:
                return {"uid": uid, "ok": True, **r.json()}
            return {"uid": uid, "ok": False, "error": r.text[:120]}
        except Exception as exc:
            return {"uid": uid, "ok": False, "error": str(exc)[:120]}


async def main() -> None:
    out_dir = Path(sys.argv[1])
    segments = json.loads(Path(sys.argv[2]).read_text())["segments"]
    speaker = sys.argv[3]
    voices = [v.strip() for v in sys.argv[4].split(",") if v.strip()]
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"{speaker}: {len(segments)} lines, {len(voices)} preset voices",
          flush=True)
    # One preset backend, so no point running these in parallel.
    sem = asyncio.Semaphore(1)

    async with httpx.AsyncClient(timeout=1800.0) as client:
        for n, voice in enumerate(voices, start=1):
            block = BASE_BLOCK + n * 1000
            t0 = time.time()
            got = await asyncio.gather(*[
                _synth(client, sem, seg, block + i, voice)
                for i, seg in enumerate(segments, start=1)])
            ok = [g for g in got if g["ok"]]
            by_uid = {g["uid"]: g for g in ok}
            clips = [{"id": block + i,
                      "file_path": by_uid[block + i]["file_path"],
                      "pause_before_ms": seg.get("pause_before_ms", 0)}
                     for i, seg in enumerate(segments, start=1)
                     if block + i in by_uid]
            name = f"{speaker.lower()}_preset_{voice}.mp3"
            r = await client.post(ASSEMBLY, json={
                "clips": clips, "output_filename": name, "normalize": True,
                "output_format": "mp3", "mp3_bitrate": "64k", "mono": True})
            info = r.json() if r.status_code == 200 else {"error": r.text[:120]}
            print(f"{name}: {len(ok)}/{len(segments)} lines, "
                  f"{time.time() - t0:.0f}s -> {info}", flush=True)

    print("\ndone", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
