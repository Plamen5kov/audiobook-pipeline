"""Tests for casting chapter speakers onto voice-bank voices.

The property that matters is stability. Secondary characters are cast
arbitrarily, which is fine, but a character whose voice changes between
regenerations would be worse than one cast badly and consistently.
"""

import importlib.util
import sys
import unittest
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "build_cast", Path(__file__).resolve().parents[2] / "tools" / "build_cast.py")
build_cast = importlib.util.module_from_spec(_SPEC)
sys.modules["build_cast"] = build_cast
_SPEC.loader.exec_module(build_cast)

BANK = ["narrator", "Jason", "Rufus", "Gary", "Farrah", "Humphrey", "Clive",
        "Sophie", "Belinda", "Emir", "Jory", "Vincent"]
SPEAKERS = ["narrator", "Jason", "Solomon", "Lu", "Josh", "Sue", "unknown"]


class CastTests(unittest.TestCase):
    def test_known_characters_keep_their_own_voice(self):
        a = build_cast.assign(SPEAKERS, BANK)
        self.assertEqual(a["narrator"], "narrator")
        self.assertEqual(a["Jason"], "Jason")

    def test_unknown_speaker_is_not_cast(self):
        self.assertNotIn("unknown", build_cast.assign(SPEAKERS, BANK))

    def test_assignment_is_stable_across_runs(self):
        first = build_cast.assign(SPEAKERS, BANK)
        second = build_cast.assign(list(reversed(SPEAKERS)), BANK)
        self.assertEqual(first, second)

    def test_narrator_and_protagonist_are_never_borrowed(self):
        a = build_cast.assign(["Solomon", "Lu", "Josh", "Sue", "Nik"], BANK)
        for speaker, voice in a.items():
            if speaker not in BANK:
                self.assertNotIn(voice, ("narrator", "Jason"))

    def test_no_two_new_characters_share_a_voice(self):
        newcomers = ["Solomon", "Lu", "Josh", "Sue", "Garnett", "Hana", "Jace"]
        a = build_cast.assign(newcomers, BANK)
        voices = list(a.values())
        self.assertEqual(len(voices), len(set(voices)))

    def test_raises_when_no_spare_voices_remain(self):
        with self.assertRaises(ValueError):
            build_cast.assign(["Solomon"], ["narrator", "Jason"])

    def test_more_newcomers_than_spares_still_assigns_all(self):
        """Collisions are unavoidable past the pool size, but nobody is dropped."""
        bank = ["narrator", "Jason", "Rufus", "Gary"]
        newcomers = [f"New{i}" for i in range(6)]
        a = build_cast.assign(newcomers, bank)
        self.assertEqual(set(a), set(newcomers))
        self.assertTrue(all(v in ("Rufus", "Gary") for v in a.values()))


if __name__ == "__main__":
    unittest.main()
