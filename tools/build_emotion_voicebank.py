"""Build a per-emotion reference set for one character.

The cloning checkpoint takes no emotion instruction, so delivery can only come
from the reference. One clip per emotion turns the corpus into a set of tones
the character can be cloned into, rather than a single fixed reading.

The pinned clip stays as neutral: it was chosen by ear, and a listening
decision should not be overridden by an alignment score.

Usage: build_emotion_voicebank.py <labelled.json> <corpus.db> <out_dir>
                                  <speaker> [pins_dir]
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

REFERENCE_PAD_S = 0.04
SAMPLE_RATE = 24000
# Below this, a "sad" set is one unlucky clip rather than a tone, and a bad
# reference is worse than falling back to neutral.
MIN_CLIPS_PER_EMOTION = 2


def _source_for(book_id: int) -> Path:
    work = Path("corpus/work")
    for slug, name in ((f"hwfwm-b{book_id}", "book.mp3"),
                       (f"hwfwm-b{book_id}", "book.m4b")):
        candidate = work / slug / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no source audio for book {book_id}")


def main() -> None:
    labelled = json.loads(Path(sys.argv[1]).read_text())
    db_path, out_dir, speaker = sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    pins_dir = Path(sys.argv[5]) if len(sys.argv) > 5 else None
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    by_emotion: dict[str, list] = defaultdict(list)
    for clip in labelled:
        by_emotion[clip.get("emotion", "neutral")].append(clip)

    print(f"{speaker}: {len(labelled)} labelled clips")
    for emotion, clips in sorted(by_emotion.items(), key=lambda kv: -len(kv[1])):
        print(f"  {emotion:<16} {len(clips)}")

    entries = []
    skipped = []
    for emotion, clips in by_emotion.items():
        if len(clips) < MIN_CLIPS_PER_EMOTION:
            skipped.append(f"{emotion}({len(clips)})")
            continue
        # Longest clip among the best-aligned: more audio gives the clone more
        # of the tone to work from.
        top = sorted(clips, key=lambda c: (-(c.get("align_score") or 0),))[:8]
        best = max(top, key=lambda c: c.get("duration_s") or 0)

        row = conn.execute(
            "SELECT book_id, audio_start_s, audio_end_s FROM segments WHERE id = ?",
            (best["id"],)).fetchone()
        if not row:
            continue
        name = f"{speaker}_{emotion}.wav"
        subprocess.run(
            ["ffmpeg", "-nostdin", "-v", "error", "-y",
             "-ss", f"{max(0.0, row['audio_start_s'] - REFERENCE_PAD_S):.3f}",
             "-to", f"{row['audio_end_s'] + REFERENCE_PAD_S:.3f}",
             "-i", str(_source_for(row["book_id"])),
             "-ac", "1", "-ar", str(SAMPLE_RATE), str(out_dir / name)],
            check=True)
        entries.append({
            "file": name, "text": best["text"], "emotion": emotion,
            "align_score": best.get("align_score"),
            "duration_s": best.get("duration_s"),
            "chapter": best.get("chapter_number"), "book": row["book_id"],
        })

    # The pinned clip is the neutral voice: it was chosen by listening.
    if pins_dir and (pins_dir / "pins.json").is_file():
        pins = json.loads((pins_dir / "pins.json").read_text())
        pin = pins.get(speaker)
        if pin and "file" in pin:
            entries = [e for e in entries if e["emotion"] != "neutral"]
            shutil.copy2(pins_dir / pin["file"], out_dir / f"{speaker}_neutral.wav")
            entries.append({"file": f"{speaker}_neutral.wav", "text": pin["text"],
                            "emotion": "neutral",
                            "align_score": pin.get("align_score"),
                            "duration_s": pin.get("duration_s"),
                            "pinned": True})

    (out_dir / "emotions.json").write_text(
        json.dumps({speaker: entries}, ensure_ascii=False, indent=1),
        encoding="utf-8")
    print(f"\n{len(entries)} emotion references -> {out_dir}")
    if skipped:
        print(f"too few clips, will fall back to neutral: {', '.join(skipped)}")


if __name__ == "__main__":
    main()
