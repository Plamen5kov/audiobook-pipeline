"""Rank a character's clips by how they were actually delivered.

Selecting training clips by the emotion of the *text* does not work: measured
across nine emotion labels, pitch and rate were uncorrelated with the label,
and the "angry" clip was lower-pitched than the neutral one. What the character
feels is not what the narrator performed.

So measure the performance. Loudness, speaking rate and pitch are cheap and
describe delivery directly, which is what a cloning reference actually carries.

Emits a manifest ordered by a subdued/intense score for a human to audition —
the ranking narrows thousands of clips to a shortlist, it does not replace the
listening.

Usage: profile_clips.py <corpus.db> <speaker> <out_json> [limit]
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

SAMPLE_RATE = 24000
MIN_SCORE = 0.93
MIN_DURATION = 2.5
MAX_DURATION = 15.0
MIN_WORDS = 8


def _source_for(book_id: int) -> Path:
    for name in ("book.mp3", "book.m4b", "book.m4a"):
        candidate = Path("corpus/work") / f"hwfwm-b{book_id}" / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no source audio for book {book_id}")


def measure(path: Path, words: int) -> dict:
    x, sr = sf.read(str(path), dtype="float32")
    if x.ndim > 1:
        x = x.mean(1)
    dur = len(x) / sr
    rms = float(20 * np.log10(np.sqrt(np.mean(x ** 2)) + 1e-9))

    # Crude autocorrelation pitch over voiced frames. Absolute accuracy does
    # not matter here; the clips only need to be comparable with each other.
    f0 = []
    win, hop = int(0.04 * sr), int(0.02 * sr)
    for i in range(0, max(0, len(x) - win), hop):
        frame = x[i:i + win]
        if np.sqrt(np.mean(frame ** 2)) < 0.01:
            continue
        frame = frame - frame.mean()
        acf = np.correlate(frame, frame, "full")[win - 1:]
        lo, hi = int(sr / 300), int(sr / 70)
        if hi >= len(acf):
            continue
        k = lo + int(np.argmax(acf[lo:hi]))
        if acf[k] > 0.3 * acf[0]:
            f0.append(sr / k)
    f0 = np.array(f0) if f0 else np.array([0.0])

    return {
        "duration_s": round(dur, 2),
        "wps": round(words / dur, 2) if dur else 0.0,
        "rms_db": round(rms, 1),
        "f0_median": round(float(np.median(f0)), 1),
        "f0_spread": round(float(np.percentile(f0, 90) - np.percentile(f0, 10)), 1)
        if len(f0) > 8 else 0.0,
    }


def main() -> None:
    db_path, speaker, out_path = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    limit = int(sys.argv[4]) if len(sys.argv) > 4 else 0

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, book_id, chapter_number, text, word_count, duration_s,
                  align_score, audio_start_s, audio_end_s
           FROM segments
           WHERE speaker = ? AND kind = 'dialogue' AND audio_start_s IS NOT NULL
             AND align_score >= ? AND word_count >= ?
             AND duration_s BETWEEN ? AND ?
           ORDER BY book_id, book_seq""",
        (speaker, MIN_SCORE, MIN_WORDS, MIN_DURATION, MAX_DURATION),
    ).fetchall()
    if limit:
        rows = rows[:limit]

    sources = {b: _source_for(b) for b in {r["book_id"] for r in rows}}
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        clip = Path(tmp) / "c.wav"
        for n, row in enumerate(rows, start=1):
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y",
                 "-ss", f"{max(0.0, row['audio_start_s'] - 0.04):.3f}",
                 "-to", f"{row['audio_end_s'] + 0.04:.3f}",
                 "-i", str(sources[row["book_id"]]),
                 "-ac", "1", "-ar", str(SAMPLE_RATE), str(clip)],
                check=True)
            m = measure(clip, row["word_count"])
            m.update({"id": row["id"], "book": row["book_id"],
                      "chapter": row["chapter_number"], "text": row["text"],
                      "align_score": row["align_score"]})
            out.append(m)
            if n % 250 == 0:
                print(f"  measured {n}/{len(rows)}", flush=True)

    # Standardise, then score. Subdued = quiet, slow, narrow pitch range.
    def z(key):
        v = np.array([o[key] for o in out], dtype=float)
        s = v.std() or 1.0
        return (v - v.mean()) / s

    zr, zw, zs = z("rms_db"), z("wps"), z("f0_spread")
    for o, a, b, c in zip(out, zr, zw, zs):
        o["subdued"] = round(float(-(a + b + c) / 3), 3)
        o["intense"] = round(float((a + b + c) / 3), 3)

    out.sort(key=lambda o: -o["subdued"])
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=1),
                        encoding="utf-8")
    print(f"\nprofiled {len(out)} clips -> {out_path}")
    print(f"  most subdued: rms {out[0]['rms_db']} dB, {out[0]['wps']} wps")
    print(f"  most intense: rms {out[-1]['rms_db']} dB, {out[-1]['wps']} wps")


if __name__ == "__main__":
    main()
