"""Export a single-speaker training set from the aligned corpus.

Fine-tuning wants exactly what alignment already produced: clean audio paired
with the words actually spoken. Nothing here needs transcription, because the
text is the source rather than a guess.

Writes 24 kHz mono WAVs plus a metadata file, which is the layout every
mainstream TTS trainer accepts.

Selection is deliberately strict. A training set is not a listening set: one
clip containing a stray word from another speaker teaches the model that the
other speaker is this character.

Usage: export_training_set.py <corpus.db> <speaker> <out_dir> [max_hours]
"""

from __future__ import annotations

import csv
import sqlite3
import subprocess
import sys
from pathlib import Path

SAMPLE_RATE = 24000
PAD_S = 0.06

MIN_SCORE = 0.93
MIN_DURATION = 1.5
MAX_DURATION = 15.0
MIN_WORDS = 4


def _source_for(book_id: int) -> Path:
    work = Path("corpus/work")
    for name in ("book.mp3", "book.m4b", "book.m4a"):
        candidate = work / f"hwfwm-b{book_id}" / name
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"no source audio for book {book_id}")


def main() -> None:
    db_path, speaker, out_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    max_hours = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0

    wav_dir = out_dir / "wavs"
    wav_dir.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """SELECT id, book_id, chapter_number, text, word_count, duration_s,
                  align_score, audio_start_s, audio_end_s
           FROM segments
           WHERE speaker = ? AND kind = 'dialogue'
             AND audio_start_s IS NOT NULL
             AND align_score >= ? AND word_count >= ?
             AND duration_s BETWEEN ? AND ?
           ORDER BY book_id, book_seq""",
        (speaker, MIN_SCORE, MIN_WORDS, MIN_DURATION, MAX_DURATION),
    ).fetchall()

    sources = {b: _source_for(b) for b in {r["book_id"] for r in rows}}
    budget = max_hours * 3600 if max_hours else float("inf")

    written = 0
    total = 0.0
    manifest = out_dir / "metadata.csv"
    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="|")
        for row in rows:
            if total + (row["duration_s"] or 0) > budget:
                break
            name = f"{speaker.lower()}_{row['id']:06d}.wav"
            start = max(0.0, row["audio_start_s"] - PAD_S)
            end = row["audio_end_s"] + PAD_S
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y",
                 "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
                 "-i", str(sources[row["book_id"]]),
                 "-ac", "1", "-ar", str(SAMPLE_RATE), str(wav_dir / name)],
                check=True)
            # LJSpeech-style: id|text|normalised text. Kept identical, since
            # the corpus text is already what was spoken.
            writer.writerow([name, row["text"], row["text"]])
            written += 1
            total += row["duration_s"] or 0
            if written % 250 == 0:
                print(f"  {written} clips, {total / 3600:.2f} h", flush=True)

    print(f"\n{speaker}: {written} clips, {total / 3600:.2f} h -> {out_dir}")
    print(f"  audio: {wav_dir}")
    print(f"  manifest: {manifest}")
    print(f"  filters: score>={MIN_SCORE}, {MIN_DURATION}-{MAX_DURATION}s, "
          f">={MIN_WORDS} words")


if __name__ == "__main__":
    main()
