"""Tests for the character registry pass.

The model is not exercised here; its output is fed in directly. What is tested
is everything that decides whether the model's opinion is allowed to touch the
corpus: the guard that refuses unsupported merges, and the rule that a stored
verdict outranks the pronoun tally when the character table is rebuilt.
"""

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from builder import db, registry  # noqa: E402

BOOK = {
    "slug": "t", "title": "T", "author": None, "series": None, "book_number": 1,
    "audio_source": "a", "epub_source": "e", "audio_duration_s": 10.0,
}

# Geller belongs to two people, Asano to one. Stated often enough to count.
PROSE = (
    "Jason Asano walked in. Jason Asano nodded. Jason Asano left.\n"
    "Humphrey Geller spoke. Humphrey Geller waited. Humphrey Geller agreed.\n"
    "Phoebe Geller frowned. Phoebe Geller turned. Phoebe Geller replied.\n"
)

LABELS = {"Jason": 40, "Humphrey": 20, "Phoebe": 12, "Gabriel": 9,
          "Gabriele": 2, "Asano": 4, "Geller": 3, "Training Rufus": 2,
          "Rufus": 15}


def verdict(name, canonical=None, gender="unknown", confidence=0.9):
    return {"name": name, "lines": LABELS.get(name, 1), "is_character": True,
            "canonical": canonical, "gender": gender,
            "confidence": confidence, "reason": "test"}


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.text_dir = Path(self.tmp.name) / "text"
        self.text_dir.mkdir()
        (self.text_dir / "ch001.txt").write_text(PROSE, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def _verdict_for(self, v):
        return registry.guard([v], LABELS, self.text_dir)[0]

    def test_label_containing_another_label_merges(self):
        v = self._verdict_for(verdict("Training Rufus", "Rufus"))
        self.assertEqual(v["verdict"], "merge")

    def test_unambiguous_surname_merges(self):
        v = self._verdict_for(verdict("Asano", "Jason"))
        self.assertEqual(v["verdict"], "merge")

    def test_shared_surname_is_vetoed(self):
        v = self._verdict_for(verdict("Geller", "Humphrey"))
        self.assertEqual(v["verdict"], "vetoed:shared-surname")

    def test_full_name_on_a_shared_surname_is_vetoed(self):
        # "Phoebe Geller" -> "Phoebe" is right, but allowing the family name to
        # carry a merge is how four Gellers become one character.
        v = self._verdict_for(verdict("Phoebe Geller", "Humphrey"))
        self.assertEqual(v["verdict"], "vetoed:shared-surname")

    def test_full_name_merges_into_its_own_given_name(self):
        # Safe even though Geller is shared: the given name carries the merge.
        labels = {**LABELS, "Phoebe Geller": 6}
        v = registry.guard([verdict("Phoebe Geller", "Phoebe")],
                           labels, self.text_dir)[0]
        self.assertEqual(v["verdict"], "merge")

    def test_full_name_cannot_merge_into_the_shared_surname(self):
        labels = {**LABELS, "Phoebe Geller": 6}
        v = registry.guard([verdict("Phoebe Geller", "Geller")],
                           labels, self.text_dir)[0]
        self.assertEqual(v["verdict"], "vetoed:shared-surname")

    def test_two_plain_names_are_never_merged(self):
        v = self._verdict_for(verdict("Gabriele", "Gabriel"))
        self.assertEqual(v["verdict"], "vetoed:unsupported")

    def test_target_outside_the_cast_is_vetoed(self):
        v = self._verdict_for(verdict("Jason", "Someone Else"))
        self.assertEqual(v["verdict"], "vetoed:unknown-target")

    def test_self_merge_is_dropped(self):
        v = self._verdict_for(verdict("Jason", "Jason"))
        self.assertIsNone(v["canonical"])
        self.assertEqual(v["verdict"], "vetoed:self")

    def test_no_canonical_is_left_alone(self):
        v = self._verdict_for(verdict("Jason", None, gender="male"))
        self.assertEqual(v["verdict"], "proposed")
        self.assertIsNone(v["canonical"])

    def test_vetoed_merge_is_not_stored_as_canonical(self):
        conn = db.connect(Path(self.tmp.name) / "c.db")
        book_id = db.upsert_book(conn, BOOK)
        v = self._verdict_for(verdict("Gabriele", "Gabriel"))
        registry.store(conn, book_id, [v], "test-model")
        stored = conn.execute(
            "SELECT canonical, verdict FROM character_registry WHERE name = ?",
            ("Gabriele",)).fetchone()
        self.assertIsNone(stored["canonical"])
        self.assertEqual(stored["verdict"], "vetoed:unsupported")
        conn.close()


class ParseTests(unittest.TestCase):
    def test_well_formed_json_parses(self):
        self.assertEqual(registry.loads('{"gender": "female"}'),
                         {"gender": "female"})

    def test_bare_enum_word_is_repaired(self):
        # What ollama actually returned for the label "Healer".
        raw = ('{\n  "is_character": false,\n  "canonical": null,\n'
               '  "gender": unknown,\n  "confidence": 0.95,\n'
               '  "reason": "a role, not a person"\n}')
        v = registry.loads(raw)
        self.assertEqual(v["gender"], "unknown")
        self.assertIs(v["is_character"], False)

    def test_bare_enum_at_end_of_object_is_repaired(self):
        self.assertEqual(registry.loads('{"gender": male}')["gender"], "male")

    def test_confidence_survives_junk(self):
        # The model's output is untrusted: a bad field must not end the batch.
        self.assertEqual(registry._num("0.9"), 0.9)
        self.assertEqual(registry._num("high"), 0.0)
        self.assertEqual(registry._num(None), 0.0)
        self.assertEqual(registry._num(7), 1.0)

    def test_is_character_survives_junk(self):
        self.assertIs(registry._bool(True), True)
        self.assertIs(registry._bool("false"), False)
        self.assertIsNone(registry._bool("maybe"))
        self.assertIsNone(registry._bool(None))

    def test_unrepairable_json_still_raises(self):
        with self.assertRaises(json.JSONDecodeError):
            registry.loads('{"gender": "male"')


class GenderOverlayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = db.connect(self.root / "c.db")
        self.book_id = db.upsert_book(self.conn, BOOK)
        for i, sp in enumerate(["Farrah"] * 4 + ["Jason"] * 4, start=1):
            self.conn.execute(
                """INSERT INTO segments (book_id, chapter_number, chapter_seq,
                       book_seq, kind, speaker, text, word_count)
                   VALUES (?,1,?,?,'dialogue',?,?,3)""",
                (self.book_id, i, 100000 + i, sp, f"line {i}"))
        # The tally reads Farrah as male: first-person narration around her
        # lines is about the narrator, who is male.
        self.conn.execute(
            """INSERT INTO chapter_characters (book_id, chapter_number, name,
                                               male_votes, female_votes)
               VALUES (?,1,'Farrah',7,2)""", (self.book_id,))
        self.conn.commit()

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def _gender(self, name):
        return self.conn.execute(
            "SELECT gender FROM characters WHERE book_id = ? AND name = ?",
            (self.book_id, name)).fetchone()[0]

    def test_tally_wins_when_there_is_no_verdict(self):
        db.rebuild_characters(self.conn, self.book_id)
        self.assertEqual(self._gender("Farrah"), "male")

    def test_verdict_overrides_the_tally(self):
        registry.store(self.conn, self.book_id,
                       [{**verdict("Farrah", gender="female"), "verdict": "proposed"}],
                       "test-model")
        db.rebuild_characters(self.conn, self.book_id)
        self.assertEqual(self._gender("Farrah"), "female")

    def test_low_confidence_verdict_does_not_override(self):
        registry.store(self.conn, self.book_id,
                       [{**verdict("Farrah", gender="female", confidence=0.2),
                         "verdict": "proposed"}], "test-model")
        db.rebuild_characters(self.conn, self.book_id)
        self.assertEqual(self._gender("Farrah"), "male")

    def test_unknown_verdict_leaves_the_tally_alone(self):
        registry.store(self.conn, self.book_id,
                       [{**verdict("Farrah", gender="unknown"), "verdict": "proposed"}],
                       "test-model")
        db.rebuild_characters(self.conn, self.book_id)
        self.assertEqual(self._gender("Farrah"), "male")


if __name__ == "__main__":
    unittest.main()
