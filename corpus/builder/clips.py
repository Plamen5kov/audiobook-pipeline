"""Cut reference clips out of the book audio on demand.

Spans are stored rather than pre-cut: a whole book is roughly ten thousand
segments, and cutting them all costs hours and gigabytes to produce material
that is only ever used a few hundred clips at a time.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
from pathlib import Path

PAD_S = 0.12


def select(conn: sqlite3.Connection, book_id: int, speaker: str | None = None,
           kind: str | None = None, chapter: int | None = None,
           min_score: float = 0.0, min_words: int = 0,
           min_duration: float = 0.0, limit: int | None = None,
           spread: bool = False) -> list[sqlite3.Row]:
    where = ["book_id = ?", "audio_start_s IS NOT NULL"]
    params: list = [book_id]
    if speaker:
        where.append("speaker = ?")
        params.append(speaker)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if chapter is not None:
        where.append("chapter_number = ?")
        params.append(chapter)
    if min_score:
        where.append("align_score >= ?")
        params.append(min_score)
    if min_words:
        where.append("word_count >= ?")
        params.append(min_words)
    if min_duration:
        where.append("duration_s >= ?")
        params.append(min_duration)

    sql = (f"SELECT * FROM segments WHERE {' AND '.join(where)} "
           f"ORDER BY book_seq")
    if limit and not spread:
        sql += f" LIMIT {int(limit)}"
    rows = conn.execute(sql, params).fetchall()

    # A plain LIMIT returns the opening chapters only. Voice work wants the
    # character across the whole book, since delivery drifts with the story.
    if spread and limit and len(rows) > limit:
        step = len(rows) / limit
        rows = [rows[int(i * step)] for i in range(limit)]
    return rows


def export(conn: sqlite3.Connection, rows: list[sqlite3.Row], source: Path,
           out_dir: Path, fmt: str = "wav", sample_rate: int = 24000) -> Path:
    """Cut *rows* to individual clips plus a manifest linking clip to text."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = out_dir / "manifest.csv"

    with manifest.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["file", "segment_id", "chapter", "book_seq", "speaker",
                         "kind", "start_s", "end_s", "duration_s", "align_score",
                         "text"])
        for row in rows:
            name = f"{row['speaker'].replace(' ', '_')}_ch{row['chapter_number']:03d}_seg{row['chapter_seq']:04d}.{fmt}"
            dest = out_dir / name
            start = max(0.0, row["audio_start_s"] - PAD_S)
            end = row["audio_end_s"] + PAD_S
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y",
                 "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
                 "-ac", "1", "-ar", str(sample_rate), str(dest)],
                check=True,
            )
            writer.writerow([name, row["id"], row["chapter_number"], row["book_seq"],
                             row["speaker"], row["kind"], round(start, 3),
                             round(end, 3), row["duration_s"], row["align_score"],
                             row["text"]])
    return manifest


def export_jsonl(conn: sqlite3.Connection, rows: list[sqlite3.Row],
                 out_path: Path) -> Path:
    """Write the selection as JSONL without cutting audio."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
    return out_path
