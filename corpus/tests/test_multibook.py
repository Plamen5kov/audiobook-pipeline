"""Two books in one database must not contaminate each other.

Most isolation comes free from book_id, but two things do not: book_seq
restarts at every book, so ordering by it alone interleaves the library, and
the full-text index needs book_id carried along to be filterable.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder import aliases, db  # noqa: E402


def _book(slug, title, number):
    return {"slug": slug, "title": title, "author": "A", "series": "S",
            "book_number": number, "audio_source": f"{slug}.mp3",
            "epub_source": f"{slug}.epub", "audio_duration_s": 1000.0}


class MultiBookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "corpus.db")

        # Both books have a chapter 1 and a character called Jason, and both
        # use the surname Asano, so any leakage shows up immediately.
        self.books = {}
        # Jason clears the anchor threshold in both books, and the line counts
        # differ so a leak between books cannot pass unnoticed.
        for slug, title, num, speakers in (
            ("b1", "Book One", 1, ["Jason"] * 6 + ["Rufus"] * 2 + ["Asano"]),
            ("b2", "Book Two", 2, ["Jason"] * 7 + ["Farrah"] * 3 + ["Asano"]),
        ):
            base = self.root / slug
            for sub in ("text", "segments", "align"):
                (base / sub).mkdir(parents=True)
            (base / "text" / "ch001.txt").write_text(
                "Jason Asano spoke. Jason Asano waited. Jason Asano left.\n",
                encoding="utf-8")
            segs = [{"id": i, "kind": "dialogue", "speaker": sp,
                     "text": f"{slug} line {i}", "attribution_source": "explicit",
                     "char_offset_start": i, "char_offset_end": i + 4}
                    for i, sp in enumerate(speakers, start=1)]
            (base / "segments" / "ch001.json").write_text(json.dumps(
                {"segments": segs, "characters": [{"name": "narrator"}]}))
            (base / "align" / "ch001.json").write_text(json.dumps({
                "chapter": 1, "audio_offset_s": 0.0, "word_count": 4 * 3,
                "mean_score": 0.9, "coverage": 0.99,
                "words": [{"word": "w", "segment_id": i, "start": i * 1.0,
                           "end": i * 1.0 + 0.5, "score": 0.9}
                          for i in range(1, len(speakers) + 1)],
                "segments": [{"segment_id": i, "start": i * 1.0,
                              "end": i * 1.0 + 0.5, "duration": 0.5,
                              "words": 1, "mean_score": 0.9}
                             for i in range(1, len(speakers) + 1)]}))

            book_id = db.upsert_book(self.conn, _book(slug, title, num))
            chapters = [{"number": 1, "title": "One",
                         "audio_start_s": 0.0, "audio_end_s": 100.0}]
            db.load_chapters(self.conn, book_id, base / "text",
                             base / "segments", base / "align", chapters)
            self.books[slug] = (book_id, base)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_segments_do_not_leak(self):
        for slug, (book_id, _) in self.books.items():
            texts = [r[0] for r in self.conn.execute(
                "SELECT text FROM segments WHERE book_id = ?", (book_id,))]
            self.assertTrue(all(t.startswith(slug) for t in texts), texts)

    def test_same_character_stays_separate_per_book(self):
        rows = self.conn.execute(
            "SELECT book_id, segment_count FROM characters WHERE name = 'Jason'"
            " ORDER BY book_id").fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual([r["segment_count"] for r in rows], [6, 7])

    def test_book_seq_collides_but_view_still_orders_by_book(self):
        collisions = self.conn.execute(
            "SELECT book_seq, COUNT(DISTINCT book_id) n FROM segments"
            " GROUP BY book_seq HAVING n > 1").fetchall()
        self.assertTrue(collisions, "expected book_seq to repeat across books")

        seen = [r["book_id"] for r in
                self.conn.execute("SELECT book_id FROM v_voice_corpus")]
        self.assertEqual(seen, sorted(seen), "view interleaved two books")

    def test_fts_can_be_scoped_to_one_book(self):
        b1 = self.books["b1"][0]
        hits = self.conn.execute(
            "SELECT book_id FROM segments_fts WHERE segments_fts MATCH 'line'"
        ).fetchall()
        self.assertEqual(len(hits), 20, "index should span both books")
        scoped = self.conn.execute(
            "SELECT COUNT(*) FROM segments_fts WHERE segments_fts MATCH 'line'"
            " AND book_id = ?", (b1,)).fetchone()[0]
        self.assertEqual(scoped, 9)

    def test_aliases_are_per_book(self):
        """The same alias may resolve differently in a different book."""
        for slug, (book_id, base) in self.books.items():
            props = aliases.derive(self.conn, book_id, base / "text")
            aliases.store(self.conn, book_id, props)
            aliases.apply(self.conn, book_id)
            db.rebuild_characters(self.conn, book_id)

        rows = self.conn.execute(
            "SELECT book_id, canonical FROM speaker_aliases WHERE alias = 'Asano'"
            " ORDER BY book_id").fetchall()
        self.assertEqual(len(rows), 2)
        for slug, (book_id, _) in self.books.items():
            names = {r[0] for r in self.conn.execute(
                "SELECT name FROM characters WHERE book_id = ?", (book_id,))}
            self.assertNotIn("Asano", names)

    def test_reloading_one_book_leaves_the_other_intact(self):
        b2_id, b2_base = self.books["b2"]
        before = self.conn.execute(
            "SELECT COUNT(*) FROM segments WHERE book_id = ?",
            (self.books["b1"][0],)).fetchone()[0]

        db.load_chapters(self.conn, b2_id, b2_base / "text",
                         b2_base / "segments", b2_base / "align",
                         [{"number": 1, "title": "One",
                           "audio_start_s": 0.0, "audio_end_s": 100.0}])

        after = self.conn.execute(
            "SELECT COUNT(*) FROM segments WHERE book_id = ?",
            (self.books["b1"][0],)).fetchone()[0]
        self.assertEqual(before, after)

    def test_rebuild_characters_scoped_to_one_book(self):
        b1 = self.books["b1"][0]
        self.conn.execute("DELETE FROM segments WHERE book_id = ?", (b1,))
        self.conn.commit()
        db.rebuild_characters(self.conn, b1)

        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM characters WHERE book_id = ?", (b1,)
        ).fetchone()[0], 0)
        self.assertGreater(self.conn.execute(
            "SELECT COUNT(*) FROM characters WHERE book_id = ?",
            (self.books["b2"][0],)).fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
