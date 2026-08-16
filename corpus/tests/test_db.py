"""Tests for corpus loading, covering the failure modes that actually occurred.

Run with: python3 -m unittest discover -s corpus/tests -t corpus
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder import db  # noqa: E402

BOOK = {
    "slug": "test", "title": "Test Book", "author": "A", "series": None,
    "book_number": 1, "audio_source": "x.mp3", "epub_source": "x.epub",
    "audio_duration_s": 600.0,
}


def _chapter(n, speakers, start):
    """One chapter: alternating narration and dialogue for *speakers*."""
    segments, words, spans = [], [], []
    t = 0.0
    for i, sp in enumerate(speakers, start=1):
        kind = "narration" if sp == "narrator" else "dialogue"
        text = f"line {i} spoken here"
        segments.append({
            "id": i, "kind": kind, "speaker": sp, "text": text,
            "attribution_source": "explicit",
            "char_offset_start": i * 10, "char_offset_end": i * 10 + 9,
        })
        spans.append({"segment_id": i, "start": t, "end": t + 2.0,
                      "duration": 2.0, "words": 4, "mean_score": 0.9})
        for w in text.split():
            words.append({"word": w, "segment_id": i, "start": t,
                          "end": t + 0.5, "score": 0.9})
            t += 0.5
        t += 0.5
    # Pronoun votes lean male, so a partial load could not flip the result by
    # accident; the test asserts the totals, not just the label.
    chars = [{"name": "narrator", "gender": "neutral"}] + [
        {"name": s, "gender": "male", "male_votes": 2, "female_votes": 0}
        for s in sorted(set(speakers) - {"narrator"})
    ]
    return (
        {"number": n, "title": f"Chapter {n}",
         "audio_start_s": start, "audio_end_s": start + 100.0},
        {"segments": segments, "characters": chars},
        {"chapter": n, "audio_offset_s": start, "word_count": len(words),
         "mean_score": 0.9, "coverage": 0.99, "words": words, "segments": spans},
    )


class LoadTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.seg_dir = root / "segments"
        self.align_dir = root / "align"
        self.text_dir = root / "text"
        for d in (self.seg_dir, self.align_dir, self.text_dir):
            d.mkdir()

        self.chapters = []
        casts = [["narrator", "Jason", "narrator", "Jason"],
                 ["narrator", "Rufus", "Jason", "Rufus"],
                 ["narrator", "Farrah", "Farrah", "narrator"]]
        for i, cast in enumerate(casts, start=1):
            meta, seg, align = _chapter(i, cast, start=(i - 1) * 100.0)
            self.chapters.append(meta)
            (self.seg_dir / f"ch{i:03d}.json").write_text(json.dumps(seg))
            (self.align_dir / f"ch{i:03d}.json").write_text(json.dumps(align))

        self.conn = db.connect(root / "corpus.db")
        self.book_id = db.upsert_book(self.conn, BOOK)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _load(self, numbers):
        chapters = [c for c in self.chapters if c["number"] in numbers]
        return db.load_chapters(self.conn, self.book_id, self.text_dir,
                                self.seg_dir, self.align_dir, chapters)

    def _counts(self):
        return {r["name"]: r["segment_count"] for r in
                self.conn.execute("SELECT name, segment_count FROM characters")}

    def test_single_pass_counts(self):
        self._load([1, 2, 3])
        self.assertEqual(self._counts(), {"Jason": 3, "Rufus": 2, "Farrah": 2})

    def test_multi_pass_matches_single_pass(self):
        """Loading a book in several passes must not leave partial totals."""
        self._load([1])
        self._load([2])
        self._load([3])
        self.assertEqual(self._counts(), {"Jason": 3, "Rufus": 2, "Farrah": 2})

    def test_reload_one_chapter_keeps_book_totals(self):
        self._load([1, 2, 3])
        self._load([1])
        self.assertEqual(self._counts(), {"Jason": 3, "Rufus": 2, "Farrah": 2})

    def test_gender_votes_accumulate_across_passes(self):
        self._load([1])
        self._load([2])
        row = self.conn.execute(
            "SELECT male_votes, gender FROM characters WHERE name = 'Jason'"
        ).fetchone()
        self.assertEqual(row["male_votes"], 4)  # two chapters, two votes each
        self.assertEqual(row["gender"], "male")

    def test_reload_is_idempotent(self):
        self._load([1, 2, 3])
        first = self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0]
        words_first = self.conn.execute("SELECT COUNT(*) FROM words").fetchone()[0]
        self._load([1, 2, 3])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0], first)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM words").fetchone()[0], words_first)

    def test_annotations_survive_reload(self):
        self._load([1, 2, 3])
        seg = self.conn.execute(
            "SELECT id FROM segments WHERE chapter_number = 1 AND chapter_seq = 2"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO segment_meta (segment_id, key, value, numeric_value, source)"
            " VALUES (?,?,?,?,?)", (seg, "emotion", "curious", 0.7, "test"))
        self.conn.commit()

        self._load([1])
        row = self.conn.execute(
            """SELECT m.value, m.numeric_value FROM segment_meta m
               JOIN segments s ON s.id = m.segment_id
               WHERE s.chapter_number = 1 AND s.chapter_seq = 2"""
        ).fetchone()
        self.assertIsNotNone(row, "annotation was lost when the chapter reloaded")
        self.assertEqual(row["value"], "curious")

    def test_annotation_dropped_when_text_changes(self):
        """A re-cut segment means the old annotation describes other words."""
        self._load([1])
        seg = self.conn.execute(
            "SELECT id FROM segments WHERE chapter_number = 1 AND chapter_seq = 2"
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO segment_meta (segment_id, key, value, source)"
            " VALUES (?,?,?,?)", (seg, "emotion", "curious", "test"))
        self.conn.commit()

        changed = json.loads((self.seg_dir / "ch001.json").read_text())
        changed["segments"][1]["text"] = "completely different words now"
        (self.seg_dir / "ch001.json").write_text(json.dumps(changed))

        self._load([1])
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM segment_meta").fetchone()[0], 0)

    def test_book_seq_orders_whole_book(self):
        self._load([1, 2, 3])
        rows = self.conn.execute(
            "SELECT chapter_number, chapter_seq, book_seq FROM segments"
            " ORDER BY book_seq").fetchall()
        expected = sorted((r["chapter_number"], r["chapter_seq"]) for r in rows)
        self.assertEqual([(r["chapter_number"], r["chapter_seq"]) for r in rows],
                         expected)

    def test_spans_are_absolute(self):
        """Segment times must be offset into the whole book, not the chapter."""
        self._load([1, 2, 3])
        row = self.conn.execute(
            "SELECT MIN(audio_start_s) a FROM segments WHERE chapter_number = 3"
        ).fetchone()
        self.assertGreaterEqual(row["a"], 200.0)


if __name__ == "__main__":
    unittest.main()
