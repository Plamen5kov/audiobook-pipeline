"""Render one passage several times, varying a single character's voice.

Everything except the character under test is held constant, so the passage can
be compared like for like: the narration and the other speakers are identical
across versions and only the candidate voice moves.

Usage:
  voice_variants.py <out_dir> <analysis.json> <speaker> <first_seg> <last_seg>
                    <variants_dir>
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
ENGINE = "qwen3-tts"
CONCURRENCY = 3
BASE_BLOCK = 40000


def text_of(seg: dict) -> str:
    return (seg.get("original_text") or seg.get("text") or "").strip()


async def _synth(client, sem, seg, uid, speaker, ref):
    payload = {
        "text": text_of(seg),
        "segment_id": uid,
        "speaker": seg.get("speaker", "narrator"),
        "engine": ENGINE,
        "emotion": seg.get("emotion", "neutral"),
    }
    if ref and seg.get("speaker") == speaker:
        payload["reference_audio_path"] = ref["path"]
        payload["reference_text"] = ref["text"]
    async with sem:
        try:
            r = await client.post(ROUTER, json=payload)
            if r.status_code == 200:
                return {"uid": uid, "ok": True, **r.json()}
            return {"uid": uid, "ok": False, "error": r.text[:120]}
        except Exception as exc:
            return {"uid": uid, "ok": False, "error": str(exc)[:120]}


async def main() -> None:
    out_dir = Path(sys.argv[1])
    analysis = Path(sys.argv[2])
    speaker = sys.argv[3]
    first, last = int(sys.argv[4]), int(sys.argv[5])
    variants_dir = Path(sys.argv[6])
    # The reference path is resolved by the TTS service, not by this driver,
    # and the two run in different containers with different mounts.
    ref_prefix = Path(sys.argv[7]) if len(sys.argv) > 7 else variants_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    segments = json.loads(analysis.read_text())["segments"][first - 1:last]
    manifest = json.loads((variants_dir / "variants.json").read_text())
    targets = sum(1 for s in segments if s.get("speaker") == speaker)
    print(f"passage: segments {first}-{last} ({len(segments)}), "
          f"{targets} by {speaker}, {len(manifest)} candidate voices", flush=True)
    if not targets:
        sys.exit(f"{speaker} does not speak in this passage")

    sem = asyncio.Semaphore(CONCURRENCY)
    results = []
    async with httpx.AsyncClient(timeout=1800.0) as client:
        for n, (clip, meta) in enumerate(sorted(manifest.items()), start=1):
            ref = {"path": str(ref_prefix / clip), "text": meta["text"]}
            block = BASE_BLOCK + n * 1000
            t0 = time.time()
            got = await asyncio.gather(*[
                _synth(client, sem, seg, block + i, speaker, ref)
                for i, seg in enumerate(segments, start=1)])
            ok = [g for g in got if g["ok"]]
            by_uid = {g["uid"]: g for g in ok}
            clips = [{"id": block + i,
                      "file_path": by_uid[block + i]["file_path"],
                      "pause_before_ms": seg.get("pause_before_ms", 0)}
                     for i, seg in enumerate(segments, start=1)
                     if block + i in by_uid]

            name = f"{speaker.lower()}_{Path(clip).stem}.mp3"
            r = await client.post(ASSEMBLY, json={
                "clips": clips, "output_filename": name, "normalize": True,
                "output_format": "mp3", "mp3_bitrate": "64k", "mono": True})
            info = r.json() if r.status_code == 200 else {"error": r.text[:120]}
            print(f"{name}: {len(ok)}/{len(segments)} segments, "
                  f"{time.time() - t0:.0f}s, ref book {meta['book']} "
                  f"ch{meta['chapter']} {meta['duration_s']:.1f}s -> {info}",
                  flush=True)
            results.append({"variant": name, "reference": clip, "meta": meta,
                            "assembly": info, "failed": len(got) - len(ok)})

    (out_dir / "variants_report.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\ndone", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
