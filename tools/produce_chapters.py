"""Synthesize, assemble and verify chapters from text-analyzer output.

Drives the running services directly rather than going through the file-server
orchestrator, because the analysis has already been done and re-running it
would spend Ollama time to reach the same answer.

Segment ids restart at 1 in every chapter while the engine names its output
files by id alone, so each chapter is given an id block of its own. Without
that, three chapters silently overwrite one another's audio.

Usage: produce_chapters.py <out_dir> <analysis.json> [more.json ...]
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import sys
import time
import wave
from pathlib import Path

import httpx

ROUTER = "http://tts-router:8010/synthesize"
ASSEMBLY = "http://audio-assembly:8005/assemble"
QA = "http://qa-verifier:8006/verify"
INTERMEDIATE = Path("/data/intermediate")
ENGINE = "qwen3-tts"
CONCURRENCY = 3
ID_BLOCK = 10000


def _wav_seconds(path: Path) -> float:
    try:
        with wave.open(str(path)) as w:
            return w.getnframes() / w.getframerate()
    except Exception:
        return 0.0


SPEAKABLE = re.compile(r"[A-Za-z]")


def segment_text(seg: dict) -> str:
    """The analyzer service calls this field original_text; the corpus builder
    renames it to text. Accept either so this driver works with both."""
    return (seg.get("original_text") or seg.get("text") or "").strip()


def is_speakable(text: str) -> bool:
    """Whether there is anything here to say.

    A segment of pure punctuation is not empty, so a truthiness check lets it
    through; the engine then returns a fraction of a second of silence that
    reads as a broken clip in the finished chapter.
    """
    return bool(SPEAKABLE.search(text))


# The voice bank is mounted into the TTS replicas, not into whichever
# container runs this driver, so look in both places.
CAST_PATHS = (Path("/voicebank/cast.json"), Path("/tmp/cast.json"))
_cast: dict = {}


def load_cast() -> dict:
    """Per-character engine routing, for voices that need another checkpoint."""
    global _cast
    for path in CAST_PATHS:
        if path.is_file():
            _cast = json.loads(path.read_text())
            print(f"cast routing from {path}: {_cast}", flush=True)
            return _cast
    print("no cast routing found — all voices use the cloning engine",
          flush=True)
    return _cast


async def _synth(client, sem, seg, uid):
    text = segment_text(seg)
    if not is_speakable(text):
        # Not a failure: there is genuinely nothing to voice. Dropping it keeps
        # a silent stub out of the assembled chapter.
        return {"uid": uid, "ok": False, "skipped": True,
                "error": "nothing speakable", "speaker": seg.get("speaker")}

    speaker = seg.get("speaker", "narrator")
    payload = {
        "text": text,
        "segment_id": uid,
        "speaker": speaker,
        "engine": ENGINE,
        "emotion": seg.get("emotion", "neutral"),
        "intensity": seg.get("intensity", 0.5),
    }
    # A character cast to a preset voice has to reach the other checkpoint;
    # the cloning replicas have no preset speakers at all.
    routing = _cast.get(speaker)
    if routing:
        payload["engine"] = routing["engine"]
        payload["qwen_speaker"] = routing["qwen_speaker"]
    async with sem:
        # Retry only transport failures. A malformed request fails the same way
        # every time, and retrying it just doubles the wait before the error
        # surfaces, which is what made a bad field name look like a hang.
        for attempt in (1, 2):
            try:
                r = await client.post(ROUTER, json=payload)
                if r.status_code == 200:
                    return {"uid": uid, "ok": True, **r.json()}
                err = f"HTTP {r.status_code}: {r.text[:120]}"
                if r.status_code < 500:
                    break
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                err = f"{type(exc).__name__}: {exc}"[:120]
            if attempt == 2:
                break
            await asyncio.sleep(5)
        return {"uid": uid, "ok": False, "error": err,
                "speaker": seg.get("speaker")}


MAX_REPAIR_ATTEMPTS = 3


async def _verify_one(client, uid: int, text: str, file_path: str) -> float:
    r = await client.post(QA, json={"segments": [
        {"id": uid, "text": text, "file_path": file_path}], "threshold": 0.85})
    if r.status_code != 200:
        return 0.0
    qa = r.json()
    # A segment that passes is not returned in either bucket.
    for bucket in ("failed", "suspect"):
        for item in qa.get(bucket, []):
            if item["id"] == uid:
                return float(item.get("similarity") or 0.0)
    return 1.0


async def repair(client, sem, flagged, segments, block, log_name) -> list[dict]:
    """Re-roll flagged segments and keep whichever take scores best.

    Generation is non-deterministic: the same short line can come back cleanly
    once and clipped the next time. Re-rolling costs seconds on a handful of
    segments and needs no model change.
    """
    fixed = []
    for uid in flagged:
        seg = segments.get(uid)
        if not seg:
            continue
        text = segment_text(seg)
        canonical = INTERMEDIATE / f"seg{uid:04d}.wav"
        if not canonical.exists():
            continue

        best_score = await _verify_one(client, uid, text, str(canonical))
        best_copy = INTERMEDIATE / f"seg{uid:04d}.best.wav"
        shutil.copy2(canonical, best_copy)
        start_score = best_score

        for _ in range(MAX_REPAIR_ATTEMPTS):
            if best_score >= 0.95:
                break
            got = await _synth(client, sem, seg, uid)
            if not got.get("ok"):
                continue
            score = await _verify_one(client, uid, text, str(canonical))
            if score > best_score:
                best_score = score
                shutil.copy2(canonical, best_copy)

        shutil.copy2(best_copy, canonical)
        best_copy.unlink(missing_ok=True)
        fixed.append({"uid": uid, "before": round(start_score, 3),
                      "after": round(best_score, 3),
                      "improved": best_score > start_score})
        print(f"{log_name}: repair seg{uid} {start_score:.3f} -> {best_score:.3f}",
              flush=True)
    return fixed


async def produce(path: Path, block: int, out_dir: Path) -> dict:
    data = json.loads(path.read_text())
    segments = data.get("segments", [])
    name = path.stem

    print(f"\n=== {name}: {len(segments)} segments ===", flush=True)
    sem = asyncio.Semaphore(CONCURRENCY)
    t0 = time.time()
    async with httpx.AsyncClient(timeout=1800.0) as client:
        results = await asyncio.gather(*[
            _synth(client, sem, seg, block + i)
            for i, seg in enumerate(segments, start=1)
        ])
    synth_s = time.time() - t0

    ok = [r for r in results if r and r["ok"]]
    failed = [r for r in results if r and not r["ok"]]
    produced = sum(_wav_seconds(Path(r["file_path"])) for r in ok)
    print(f"{name}: synthesized {len(ok)}/{len(segments)} in {synth_s:.0f}s, "
          f"{produced / 60:.1f} min audio, RTF {synth_s / max(produced, 1):.2f}",
          flush=True)
    if failed:
        print(f"{name}: FAILURES {[(f['uid'], f['error'][:60]) for f in failed][:5]}",
              flush=True)

    by_uid = {r["uid"]: r for r in ok}
    clips = [{"id": block + i, "file_path": by_uid[block + i]["file_path"],
              "pause_before_ms": seg.get("pause_before_ms", 0)}
             for i, seg in enumerate(segments, start=1) if block + i in by_uid]

    report = {"chapter": name, "segments": len(segments), "synthesized": len(ok),
              "failed": [f["uid"] for f in failed], "synth_seconds": round(synth_s),
              "audio_minutes": round(produced / 60, 2)}

    async with httpx.AsyncClient(timeout=3600.0) as client:
        checks = [{"id": block + i, "text": segment_text(seg),
                   "file_path": by_uid[block + i]["file_path"]}
                  for i, seg in enumerate(segments, start=1) if block + i in by_uid]
        try:
            r = await client.post(QA, json={"segments": checks, "threshold": 0.85})
            qa = r.json() if r.status_code == 200 else {"error": r.text[:200]}

            # Repair before assembly, so the finished chapter contains the best
            # take rather than the first one.
            flagged = [s["id"] for s in
                       qa.get("failed", []) + qa.get("suspect", [])]
            if flagged:
                by_id = {block + i: seg
                         for i, seg in enumerate(segments, start=1)}
                report["repairs"] = await repair(
                    client, sem, flagged, by_id, block, name)
                r = await client.post(
                    QA, json={"segments": checks, "threshold": 0.85})
                qa = r.json() if r.status_code == 200 else qa
            report["qa"] = qa
            report["qa_counts"] = {
                "checked": qa.get("checked", 0),
                "passed": qa.get("passed", 0),
                "failed": qa.get("failed_count", 0),
                "suspect": qa.get("suspect_count", 0),
                "mean_similarity": qa.get("mean_similarity", 0.0),
                "missing": len(qa.get("missing_files", [])),
            }
            print(f"{name}: QA {report['qa_counts']}", flush=True)
        except Exception as exc:
            report["qa"] = f"FAILED {exc}"
            print(f"{name}: QA FAILED {exc}", flush=True)

        try:
            r = await client.post(ASSEMBLY, json={
                "clips": clips, "output_filename": f"{name}.mp3",
                "normalize": True, "output_format": "mp3", "mp3_bitrate": "64k",
                "mono": True})
            report["assembly"] = r.json() if r.status_code == 200 else r.text[:200]
            print(f"{name}: assembled -> {report['assembly']}", flush=True)
        except Exception as exc:
            report["assembly"] = f"FAILED {exc}"
            print(f"{name}: assembly FAILED {exc}", flush=True)

    (out_dir / f"{name}.report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


async def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    load_cast()
    reports = []
    for n, arg in enumerate(sys.argv[2:], start=1):
        reports.append(await produce(Path(arg), n * ID_BLOCK, out_dir))
    (out_dir / "summary.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n=== summary ===", flush=True)
    for r in reports:
        print(f"{r['chapter']}: {r['synthesized']}/{r['segments']} segments, "
              f"{r['audio_minutes']} min, QA {r.get('qa_counts')}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
