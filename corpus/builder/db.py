"""Load per-chapter artifacts into the SQLite corpus."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

SCHEMA = Path(__file__).resolve().parents[1] / "schema.sql"
WORD_RE = re.compile(r"[A-Za-z']+")


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    dropped = _drop_stale(conn)
    conn.executescript(SCHEMA.read_text())
    _migrate(conn, dropped)
    return conn


# Objects the schema creates with IF NOT EXISTS, so a changed definition is
# ignored on an existing database unless the old one is dropped first. Each
# entry names a marker that must appear in the stored SQL for it to be current.
_EXPECTED = {
    ("view", "v_voice_corpus"): "ORDER BY s.book_id",
    ("table", "segments_fts"): "book_id UNINDEXED",
}


def _drop_stale(conn: sqlite3.Connection) -> set[str]:
    dropped: set[str] = set()
    for (kind, name), marker in _EXPECTED.items():
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE name = ?", (name,)).fetchone()
        if row and marker not in (row[0] or ""):
            conn.execute(f"DROP {'VIEW' if kind == 'view' else 'TABLE'} IF EXISTS {name}")
            dropped.add(name)
    if dropped:
        conn.commit()
    return dropped


def _migrate(conn: sqlite3.Connection, dropped: set[str] = frozenset()) -> None:
    """Add columns introduced after a corpus was first built."""
    have = {r["name"] for r in conn.execute("PRAGMA table_info(segments)")}
    if "speaker_raw" not in have:
        conn.execute("ALTER TABLE segments ADD COLUMN speaker_raw TEXT")
        conn.commit()
    if "segments_fts" in dropped:
        # A recreated external-content index starts empty.
        conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
        conn.commit()


def upsert_book(conn: sqlite3.Connection, book: dict) -> int:
    conn.execute(
        """INSERT INTO books (slug, title, author, series, book_number,
                              audio_source, epub_source, audio_duration_s)
           VALUES (:slug, :title, :author, :series, :book_number,
                   :audio_source, :epub_source, :audio_duration_s)
           ON CONFLICT(slug) DO UPDATE SET
               title = excluded.title,
               audio_source = excluded.audio_source,
               epub_source = excluded.epub_source,
               audio_duration_s = excluded.audio_duration_s""",
        book,
    )
    return conn.execute("SELECT id FROM books WHERE slug = ?", (book["slug"],)).fetchone()[0]


def load_chapters(conn: sqlite3.Connection, book_id: int, text_dir: Path,
                  seg_dir: Path, align_dir: Path, chapters: list[dict]) -> dict:
    """Load every chapter that has both segments and alignment on disk.

    Reloading a chapter replaces it wholesale, so a re-run after an improved
    segmentation pass converges rather than accumulating duplicates.
    """
    stats = Counter()

    for ch in chapters:
        n = ch["number"]
        seg_file = seg_dir / f"ch{n:03d}.json"
        align_file = align_dir / f"ch{n:03d}.json"
        if not seg_file.exists():
            stats["skipped_no_segments"] += 1
            continue

        parsed = json.loads(seg_file.read_text())
        segments = parsed["segments"]
        alignment = json.loads(align_file.read_text()) if align_file.exists() else None

        spans = {}
        words_by_seg: dict[int, list] = {}
        offset = 0.0
        if alignment:
            offset = alignment.get("audio_offset_s", ch["audio_start_s"])
            spans = {s["segment_id"]: s for s in alignment["segments"]}
            for w in alignment["words"]:
                words_by_seg.setdefault(w["segment_id"], []).append(w)

        # Annotations are keyed on a surrogate id that reloading regenerates,
        # so they are carried across by segment position and text. A segment
        # whose text changed has genuinely been re-cut, and its old annotation
        # would be describing different words, so it is dropped on purpose.
        carried = conn.execute(
            """SELECT s.chapter_seq, s.text, m.key, m.value, m.numeric_value,
                      m.source, m.created_at
               FROM segment_meta m JOIN segments s ON s.id = m.segment_id
               WHERE s.book_id = ? AND s.chapter_number = ?""",
            (book_id, n),
        ).fetchall()
        conn.execute("DELETE FROM segments WHERE book_id = ? AND chapter_number = ?",
                     (book_id, n))
        conn.execute(
            """INSERT INTO chapters (book_id, number, title, audio_start_s,
                                     audio_end_s, word_count, segment_count,
                                     align_mean_score, align_coverage)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(book_id, number) DO UPDATE SET
                   title = excluded.title,
                   word_count = excluded.word_count,
                   segment_count = excluded.segment_count,
                   align_mean_score = excluded.align_mean_score,
                   align_coverage = excluded.align_coverage""",
            (book_id, n, ch.get("title"), ch["audio_start_s"], ch["audio_end_s"],
             alignment["word_count"] if alignment else None, len(segments),
             alignment["mean_score"] if alignment else None,
             alignment["coverage"] if alignment else None),
        )

        # book_seq leaves room between chapters so a later re-segmentation of
        # one chapter never has to renumber the rest of the book.
        base = n * 100_000
        for seq, seg in enumerate(segments, start=1):
            span = spans.get(seg["id"])
            start = round(offset + span["start"], 3) if span else None
            end = round(offset + span["end"], 3) if span else None
            cur = conn.execute(
                """INSERT INTO segments (book_id, chapter_number, chapter_seq,
                        book_seq, kind, speaker, attribution_source, text,
                        word_count, char_start, char_end, audio_start_s,
                        audio_end_s, duration_s, align_score, align_words)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (book_id, n, seq, base + seq, seg["kind"], seg["speaker"],
                 seg.get("attribution_source"), seg["text"],
                 len(WORD_RE.findall(seg["text"])),
                 seg.get("char_offset_start"), seg.get("char_offset_end"),
                 start, end,
                 round(span["duration"], 3) if span else None,
                 span["mean_score"] if span else None,
                 span["words"] if span else None),
            )
            seg_id = cur.lastrowid
            stats["segments"] += 1
            if span:
                stats["aligned"] += 1

            rows = [
                (seg_id, i, w["word"], round(offset + w["start"], 3),
                 round(offset + w["end"], 3), w["score"])
                for i, w in enumerate(words_by_seg.get(seg["id"], []))
            ]
            if rows:
                conn.executemany(
                    "INSERT INTO words (segment_id, idx, word, start_s, end_s, score)"
                    " VALUES (?,?,?,?,?,?)", rows,
                )

        if carried:
            conn.executemany(
                """INSERT OR IGNORE INTO segment_meta
                       (segment_id, key, value, numeric_value, source, created_at)
                   SELECT s.id, ?, ?, ?, ?, ?
                   FROM segments s
                   WHERE s.book_id = ? AND s.chapter_number = ?
                     AND s.chapter_seq = ? AND s.text = ?""",
                [(m["key"], m["value"], m["numeric_value"], m["source"],
                  m["created_at"], book_id, n, m["chapter_seq"], m["text"])
                 for m in carried],
            )
            kept = conn.execute(
                """SELECT COUNT(*) FROM segment_meta m JOIN segments s
                   ON s.id = m.segment_id
                   WHERE s.book_id = ? AND s.chapter_number = ?""",
                (book_id, n),
            ).fetchone()[0]
            stats["annotations_carried"] += kept
            stats["annotations_dropped"] += len(carried) - kept

        for c in parsed["characters"]:
            if c["name"] == "narrator":
                continue
            conn.execute(
                """INSERT INTO chapter_characters (book_id, chapter_number, name,
                                                   male_votes, female_votes)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(book_id, chapter_number, name) DO UPDATE SET
                       male_votes = excluded.male_votes,
                       female_votes = excluded.female_votes""",
                (book_id, n, c["name"], c.get("male_votes", 0),
                 c.get("female_votes", 0)),
            )

        stats["chapters"] += 1

    # Reloading rewrites speakers from the raw attribution, so any stored alias
    # mapping has to be reapplied or a reload would silently un-merge characters.
    from . import aliases
    aliases.apply(conn, book_id)

    rebuild_characters(conn, book_id)
    conn.execute("INSERT INTO segments_fts(segments_fts) VALUES('rebuild')")
    conn.commit()
    return dict(stats)


def rebuild_characters(conn: sqlite3.Connection, book_id: int) -> int:
    """Recompute the character table from what is actually stored.

    Deriving these totals from the chapters loaded in one run made them a
    patchwork whenever a book was loaded in several passes, which is the normal
    case for a long book. Gender is the field that matters: it drives voice
    casting, and a partial pronoun tally can flip it.
    """
    conn.execute("DELETE FROM characters WHERE book_id = ?", (book_id,))
    conn.execute(
        """INSERT INTO characters (book_id, name, gender, male_votes,
                                   female_votes, segment_count)
           SELECT s.book_id, s.speaker,
                  CASE WHEN COALESCE(v.male, 0) > COALESCE(v.female, 0) THEN 'male'
                       WHEN COALESCE(v.female, 0) > COALESCE(v.male, 0) THEN 'female'
                       ELSE 'unknown' END,
                  COALESCE(v.male, 0), COALESCE(v.female, 0), COUNT(*)
           FROM segments s
           LEFT JOIN (SELECT book_id, name,
                             SUM(male_votes) male, SUM(female_votes) female
                      FROM chapter_characters GROUP BY book_id, name) v
                  ON v.book_id = s.book_id AND v.name = s.speaker
           WHERE s.book_id = ? AND s.kind != 'heading'
             AND s.speaker NOT IN ('narrator', 'unknown')
           GROUP BY s.book_id, s.speaker""",
        (book_id,),
    )
    # The registry pass judged these from the prose; the tally above only
    # counts pronouns near a character's lines, which in first-person books
    # counts the narrator's. Where both have an opinion, the judgement wins.
    from .registry import APPLY_CONFIDENCE

    conn.execute(
        """UPDATE characters SET gender = (
               SELECT r.gender FROM character_registry r
               WHERE r.book_id = characters.book_id AND r.name = characters.name
                 AND r.gender IN ('male', 'female') AND r.confidence >= :conf)
           WHERE book_id = :book AND EXISTS (
               SELECT 1 FROM character_registry r
               WHERE r.book_id = characters.book_id AND r.name = characters.name
                 AND r.gender IN ('male', 'female') AND r.confidence >= :conf)""",
        {"book": book_id, "conf": APPLY_CONFIDENCE},
    )
    conn.commit()
    return conn.execute("SELECT COUNT(*) FROM characters WHERE book_id = ?",
                        (book_id,)).fetchone()[0]
