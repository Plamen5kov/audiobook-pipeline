"""Tests for speaker alias resolution.

The guard that matters is the ambiguous surname: merging a shared family name
would silently fuse several characters into one and corrupt every voice corpus
built from them.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder import aliases, db  # noqa: E402

BOOK = {
    "slug": "t", "title": "T", "author": None, "series": None, "book_number": 1,
    "audio_source": "a", "epub_source": "e", "audio_duration_s": 10.0,
}

# Two Gellers and one unambiguous surname, stated often enough to count.
PROSE = (
    "Jason Asano walked in. Jason Asano nodded. Jason Asano left.\n"
    "Humphrey Geller spoke. Humphrey Geller waited. Humphrey Geller agreed.\n"
    "Phoebe Geller frowned. Phoebe Geller turned. Phoebe Geller replied.\n"
)


class AliasTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.text_dir = self.root / "text"
        self.text_dir.mkdir()
        (self.text_dir / "ch001.txt").write_text(PROSE, encoding="utf-8")

        self.conn = db.connect(self.root / "c.db")
        self.book_id = db.upsert_book(self.conn, BOOK)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _seed(self, speakers: list[str]) -> None:
        for i, sp in enumerate(speakers, start=1):
            self.conn.execute(
                """INSERT INTO segments (book_id, chapter_number, chapter_seq,
                       book_seq, kind, speaker, text, word_count)
                   VALUES (?,1,?,?,'dialogue',?,?,3)""",
                (self.book_id, i, 100000 + i, sp, f"line {i}"))
        self.conn.commit()

    def _map(self):
        return {p["alias"]: p["canonical"]
                for p in aliases.derive(self.conn, self.book_id, self.text_dir)}

    def test_unambiguous_surname_merges(self):
        self._seed(["Jason"] * 6 + ["Asano"] * 2)
        self.assertEqual(self._map().get("Asano"), "Jason")

    def test_shared_surname_never_merges(self):
        """Geller belongs to two people, so it must stay unresolved."""
        self._seed(["Humphrey"] * 6 + ["Phoebe"] * 6 + ["Geller"] * 3)
        self.assertNotIn("Geller", self._map())

    def test_full_name_folds_into_first_name(self):
        self._seed(["Phoebe"] * 6 + ["Phoebe Geller"] * 2)
        self.assertEqual(self._map().get("Phoebe Geller"), "Phoebe")

    def test_suffix_noise_folds_in(self):
        self._seed(["Jason"] * 6 + ["Nightingale Jason"])
        self.assertEqual(self._map().get("Nightingale Jason"), "Jason")

    def test_suffix_wins_over_more_frequent_noisy_label(self):
        """The cleaner label is canonical even when it is the rarer one."""
        self._seed(["Fire Fist"] * 2 + ["Ointment Fire Fist"] * 7)
        self.assertEqual(self._map().get("Ointment Fire Fist"), "Fire Fist")

    def test_chain_resolves_to_person_not_surname(self):
        self._seed(["Jason"] * 6 + ["Asano"] * 2 + ["Jason Asano"] * 2)
        m = self._map()
        self.assertEqual(m.get("Asano"), "Jason")
        self.assertEqual(m.get("Jason Asano"), "Jason")

    def test_no_self_mapping(self):
        self._seed(["Jason"] * 6 + ["Asano"] * 2)
        for alias, canonical in self._map().items():
            self.assertNotEqual(alias, canonical)

    def test_apply_preserves_raw_label(self):
        self._seed(["Jason"] * 6 + ["Asano"] * 2)
        aliases.store(self.conn, self.book_id,
                      aliases.derive(self.conn, self.book_id, self.text_dir))
        aliases.apply(self.conn, self.book_id)
        row = self.conn.execute(
            "SELECT speaker, speaker_raw FROM segments WHERE speaker_raw = 'Asano'"
        ).fetchone()
        self.assertEqual(row["speaker"], "Jason")
        self.assertEqual(row["speaker_raw"], "Asano")

    def test_apply_is_idempotent(self):
        self._seed(["Jason"] * 6 + ["Asano"] * 2)
        props = aliases.derive(self.conn, self.book_id, self.text_dir)
        aliases.store(self.conn, self.book_id, props)
        aliases.apply(self.conn, self.book_id)
        aliases.apply(self.conn, self.book_id)
        counts = {r["speaker"]: r["n"] for r in self.conn.execute(
            "SELECT speaker, COUNT(*) n FROM segments GROUP BY speaker")}
        self.assertEqual(counts, {"Jason": 8})

    def test_derive_is_stable_after_apply(self):
        """Deriving again must see raw labels, not the merged ones."""
        self._seed(["Jason"] * 6 + ["Asano"] * 2)
        props = aliases.derive(self.conn, self.book_id, self.text_dir)
        aliases.store(self.conn, self.book_id, props)
        aliases.apply(self.conn, self.book_id)
        self.assertEqual(self._map().get("Asano"), "Jason")


class ReloadTests(unittest.TestCase):
    """A reload rewrites speakers from the raw attribution, so the stored
    mapping has to be reapplied or characters silently un-merge."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.text_dir, self.seg_dir, self.align_dir = (
            root / "text", root / "segments", root / "align")
        for d in (self.text_dir, self.seg_dir, self.align_dir):
            d.mkdir()
        (self.text_dir / "ch001.txt").write_text(PROSE, encoding="utf-8")

        speakers = ["Jason"] * 6 + ["Asano"] * 2
        segs = [{"id": i, "kind": "dialogue", "speaker": sp,
                 "text": f"line {i}", "attribution_source": "explicit",
                 "char_offset_start": i, "char_offset_end": i + 5}
                for i, sp in enumerate(speakers, start=1)]
        (self.seg_dir / "ch001.json").write_text(json.dumps(
            {"segments": segs, "characters": [{"name": "narrator"}]}))

        self.chapters = [{"number": 1, "title": "One",
                          "audio_start_s": 0.0, "audio_end_s": 10.0}]
        self.conn = db.connect(root / "c.db")
        self.book_id = db.upsert_book(self.conn, BOOK)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _load(self):
        return db.load_chapters(self.conn, self.book_id, self.text_dir,
                                self.seg_dir, self.align_dir, self.chapters)

    def test_aliases_reapplied_on_reload(self):
        self._load()
        aliases.store(self.conn, self.book_id,
                      aliases.derive(self.conn, self.book_id, self.text_dir))
        aliases.apply(self.conn, self.book_id)
        db.rebuild_characters(self.conn, self.book_id)

        self._load()  # reload must not resurrect "Asano" as its own character
        names = {r["name"] for r in
                 self.conn.execute("SELECT name FROM characters")}
        self.assertNotIn("Asano", names)
        self.assertEqual(
            self.conn.execute(
                "SELECT segment_count FROM characters WHERE name='Jason'"
            ).fetchone()[0], 8)


if __name__ == "__main__":
    unittest.main()
