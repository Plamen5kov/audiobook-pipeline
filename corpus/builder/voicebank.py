"""Export cloning references: a few clean clips per character, plus their text.

Qwen3-TTS clones best in in-context mode, which needs the reference audio *and*
the words spoken in it. That is normally the hard part, since a clip taken from
an audiobook comes with no transcript and ASR guesses introduce errors that the
clone then inherits. Here the alignment already established exactly which words
occupy which span, so the text is exact.

Clips are cut tighter than listening clips: the reference should contain the
character and nothing else, so a neighbouring word bleeding in at the edges
matters more than a slightly clipped consonant.
"""

from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

REFERENCE_PAD_S = 0.04
SAMPLE_RATE = 24000

# Long enough for the model to hear the voice, short enough to stay clean.
MIN_DURATION = 4.0
MAX_DURATION = 12.0
MIN_WORDS = 10
MIN_SCORE = 0.94


def candidates(conn: sqlite3.Connection, book_id: int, speaker: str,
               limit: int) -> list[sqlite3.Row]:
    """Best reference clips for one character, spread across the book.

    Spread matters: a character's delivery drifts with the story, and several
    clips from one scene would encode that scene rather than the voice.
    """
    rows = conn.execute(
        """SELECT id, speaker, chapter_number, chapter_seq, text, word_count,
                  audio_start_s, audio_end_s, duration_s, align_score
           FROM segments
           WHERE book_id = ? AND speaker = ? AND kind = 'dialogue'
             AND audio_start_s IS NOT NULL
             AND align_score >= ? AND word_count >= ?
             AND duration_s BETWEEN ? AND ?
           ORDER BY book_seq""",
        (book_id, speaker, MIN_SCORE, MIN_WORDS, MIN_DURATION, MAX_DURATION),
    ).fetchall()
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def narrator_candidates(conn: sqlite3.Connection, book_id: int,
                        limit: int) -> list[sqlite3.Row]:
    rows = conn.execute(
        """SELECT id, speaker, chapter_number, chapter_seq, text, word_count,
                  audio_start_s, audio_end_s, duration_s, align_score
           FROM segments
           WHERE book_id = ? AND speaker = 'narrator' AND kind = 'narration'
             AND audio_start_s IS NOT NULL
             AND align_score >= ? AND word_count >= ?
             AND duration_s BETWEEN ? AND ?
           ORDER BY book_seq""",
        (book_id, MIN_SCORE, MIN_WORDS, MIN_DURATION, MAX_DURATION),
    ).fetchall()
    if len(rows) <= limit:
        return rows
    step = len(rows) / limit
    return [rows[int(i * step)] for i in range(limit)]


def _cut(source: Path, row: sqlite3.Row, dest: Path) -> None:
    start = max(0.0, row["audio_start_s"] - REFERENCE_PAD_S)
    end = row["audio_end_s"] + REFERENCE_PAD_S
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-y",
         "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(source),
         "-ac", "1", "-ar", str(SAMPLE_RATE), str(dest)],
        check=True,
    )


def export(conn: sqlite3.Connection, book_id: int, source: Path, out_dir: Path,
           per_character: int = 3, min_clips: int = 2) -> dict:
    """Write reference clips and a manifest mapping character to (clip, text)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    speakers = [r[0] for r in conn.execute(
        """SELECT speaker FROM segments
           WHERE book_id = ? AND kind = 'dialogue' AND speaker != 'unknown'
           GROUP BY speaker ORDER BY COUNT(*) DESC""", (book_id,))]

    bank: dict[str, list[dict]] = {}
    for speaker in ["narrator"] + speakers:
        rows = (narrator_candidates(conn, book_id, per_character)
                if speaker == "narrator"
                else candidates(conn, book_id, speaker, per_character))
        if len(rows) < min_clips:
            continue

        safe = speaker.replace(" ", "_").replace("/", "_")
        entries = []
        for i, row in enumerate(rows, start=1):
            name = f"{safe}_{i:02d}.wav"
            _cut(source, row, out_dir / name)
            entries.append({
                "file": name,
                "text": row["text"],
                "duration_s": row["duration_s"],
                "align_score": row["align_score"],
                "chapter": row["chapter_number"],
                "segment_id": row["id"],
            })
        bank[speaker] = entries

    manifest = out_dir / "voicebank.json"
    manifest.write_text(json.dumps(
        {"book_id": book_id, "sample_rate": SAMPLE_RATE, "voices": bank},
        ensure_ascii=False, indent=1), encoding="utf-8")
    return bank
