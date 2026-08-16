"""Measure TTS throughput against the router.

Reports the real-time factor, which is generation wall clock divided by the
audio actually produced. Audio length is measured from the WAV frames, not
taken from the service's own timing field, which reports generation wall clock
including time spent waiting on the inference lock.

Usage: python3 bench_tts.py [--concurrency N] [--count N] [--engine NAME]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


async def _one(client: httpx.AsyncClient, url: str, seg: dict, engine: str,
               out_dir: Path, sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.monotonic()
        resp = await client.post(url, json={
            "text": seg["text"],
            "segment_id": seg["segment_id"],
            "speaker": seg["speaker"],
            "engine": engine,
            "emotion": "neutral",
            "intensity": 0.5,
        })
        elapsed = time.monotonic() - t0

    if resp.status_code != 200:
        return {"id": seg["segment_id"], "ok": False, "elapsed": elapsed,
                "detail": resp.text[:120]}

    produced = resp.json().get("file_path", "")
    return {"id": seg["segment_id"], "ok": True, "elapsed": elapsed,
            "path": produced, "reference_s": seg.get("reference_s") or 0.0}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--router", default="http://localhost:8010/synthesize")
    ap.add_argument("--engine", default="qwen3-tts")
    ap.add_argument("--concurrency", type=int, default=1)
    ap.add_argument("--count", type=int, default=12)
    ap.add_argument("--segments", type=Path, default=HERE / "bench_segments.json")
    ap.add_argument("--audio-dir", type=Path, default=Path("/data/intermediate"))
    args = ap.parse_args()

    segments = json.loads(args.segments.read_text())[:args.count]
    sem = asyncio.Semaphore(args.concurrency)

    print(f"engine={args.engine} concurrency={args.concurrency} "
          f"segments={len(segments)}", flush=True)

    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=1800.0) as client:
        results = await asyncio.gather(*[
            _one(client, args.router, s, args.engine, args.audio_dir, sem)
            for s in segments
        ])
    wall = time.monotonic() - t0

    ok = [r for r in results if r["ok"]]
    failed = [r for r in results if not r["ok"]]

    produced = 0.0
    for r in ok:
        p = Path(r["path"])
        if not p.is_absolute():
            p = args.audio_dir / p
        produced += _wav_seconds(p)

    reference = sum(r.get("reference_s", 0.0) for r in ok)

    print(f"\nwall clock      : {wall:.1f} s")
    print(f"segments ok     : {len(ok)}/{len(results)}")
    if failed:
        print(f"failures        : {[(f['id'], f['detail']) for f in failed][:3]}")
    print(f"audio produced  : {produced:.1f} s")
    print(f"human reference : {reference:.1f} s")
    if produced > 0:
        print(f"RTF             : {wall / produced:.2f}  (lower is faster)")
        print(f"throughput      : {produced / wall:.2f} s audio per s wall")
    if reference > 0 and produced > 0:
        print(f"length vs human : {100 * (produced / reference - 1):+.1f}%")
    per = sorted(r["elapsed"] for r in ok)
    if per:
        print(f"per-segment     : median {per[len(per) // 2]:.1f} s, "
              f"max {per[-1]:.1f} s")


if __name__ == "__main__":
    asyncio.run(main())
