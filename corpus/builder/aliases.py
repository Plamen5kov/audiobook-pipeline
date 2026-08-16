"""Resolve speaker labels that refer to the same character.

Attribution produces three kinds of duplicate: a real name with stray words
attached ("Training Rufus"), a full name beside a bare first name ("Phoebe
Geller" and "Phoebe"), and a bare surname used alone ("Ventress").

Surnames are the dangerous case, because families share them. In this book
Geller belongs to four different people, so the surname rule fires only when
the whole book's text shows a surname following exactly one first name.
Evidence comes from the prose, not from the labels, since the labels are the
thing being corrected.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

FULL_NAME = re.compile(r"\b([A-Z][a-z]+) ([A-Z][a-z]+)\b")
NOT_CHARACTERS = ("narrator", "unknown")

MIN_ANCHOR_LINES = 5
MIN_SURNAME_EVIDENCE = 3


def _labels(conn: sqlite3.Connection, book_id: int) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute(
        """SELECT COALESCE(speaker_raw, speaker), COUNT(*)
           FROM segments WHERE book_id = ? AND kind != 'heading'
           GROUP BY 1""", (book_id,))
        if r[0] not in NOT_CHARACTERS}


def anchors_of(labels: dict[str, int]) -> set[str]:
    """Single-word labels frequent enough to be treated as real characters."""
    return {s for s in labels
            if len(s.split()) == 1 and labels[s] >= MIN_ANCHOR_LINES}


def surname_owners(text_dir: Path, anchors: set[str]) -> tuple[dict[str, dict[str, int]], set[str]]:
    """Which surname belongs to whom, according to the prose.

    A surname shared by several characters is unusable: this book has four
    Gellers. Returns the owners per surname and the set that is ambiguous.
    """
    text = " ".join(p.read_text(encoding="utf-8")
                    for p in sorted(text_dir.glob("*.txt")))
    pairs = Counter(FULL_NAME.findall(text))
    by_surname: dict[str, dict[str, int]] = defaultdict(dict)
    for (first, last), count in pairs.items():
        if first in anchors and count >= MIN_SURNAME_EVIDENCE:
            by_surname[last][first] = count
    return by_surname, {s for s, firsts in by_surname.items() if len(firsts) > 1}


def derive(conn: sqlite3.Connection, book_id: int, text_dir: Path) -> list[dict]:
    """Propose ``alias -> canonical`` mappings with the evidence for each."""
    labels = _labels(conn, book_id)
    anchors = anchors_of(labels)
    proposals: dict[str, dict] = {}

    by_surname, ambiguous = surname_owners(text_dir, anchors)

    # 1. A bare surname the prose is unanimous about. Frequency is no defence
    #    here: a surname used often on its own is still that person's surname.
    for surname, firsts in by_surname.items():
        if surname in labels and surname not in ambiguous:
            first = next(iter(firsts))
            if first != surname:
                proposals[surname] = {"canonical": first, "reason": "surname",
                                      "evidence": firsts[first]}

    # 2. A label ending in another label is that label with noise on the front:
    #    "Training Rufus", "Ointment Fire Fist". The suffix is the cleaner form
    #    by construction, so it wins regardless of which label is commoner.
    for label in labels:
        tokens = label.split()
        if len(tokens) < 2 or label in proposals:
            continue
        for cut in range(1, len(tokens)):
            tail = " ".join(tokens[cut:])
            if tail in labels and tail not in ambiguous:
                proposals[label] = {"canonical": tail, "reason": "suffix",
                                    "evidence": labels[tail]}
                break

    # 3. "First Last" where First is a known character is that character.
    for label in labels:
        tokens = label.split()
        if len(tokens) == 2 and tokens[0] in anchors and label not in proposals:
            proposals[label] = {"canonical": tokens[0], "reason": "full_name",
                                "evidence": labels[tokens[0]]}

    # Spelling variants are deliberately not merged. "Gabriele" sits one letter
    # from both "Gabriel" and "Gabrielle", who are different people, and no
    # evidence in the text distinguishes them. Leaving a rare label alone costs
    # a few lines; merging two characters corrupts both their voice corpora.

    return _resolve(proposals, labels)


def _resolve(proposals: dict[str, dict], labels: dict[str, int]) -> list[dict]:
    """Follow chains to a final canonical name, refusing to loop."""
    out = []
    for alias, p in proposals.items():
        seen, target = {alias}, p["canonical"]
        while target in proposals and target not in seen:
            seen.add(target)
            target = proposals[target]["canonical"]
        if target == alias:
            continue
        out.append({"alias": alias, "canonical": target, "reason": p["reason"],
                    "evidence": p["evidence"], "lines": labels[alias]})
    return sorted(out, key=lambda r: (-r["lines"], r["alias"]))


def store(conn: sqlite3.Connection, book_id: int, proposals: list[dict]) -> None:
    conn.executemany(
        """INSERT INTO speaker_aliases (book_id, alias, canonical, reason, evidence)
           VALUES (?,?,?,?,?)
           ON CONFLICT(book_id, alias) DO UPDATE SET
               canonical = excluded.canonical,
               reason = excluded.reason,
               evidence = excluded.evidence""",
        [(book_id, p["alias"], p["canonical"], p["reason"], p["evidence"])
         for p in proposals],
    )
    conn.commit()


def apply(conn: sqlite3.Connection, book_id: int) -> int:
    """Rewrite segment speakers to their canonical name.

    The label attribution actually produced is kept in ``speaker_raw`` so the
    mapping stays auditable and can be revised without re-running the build.
    """
    conn.execute(
        """UPDATE segments
           SET speaker_raw = COALESCE(speaker_raw, speaker),
               speaker = (SELECT canonical FROM speaker_aliases a
                          WHERE a.book_id = segments.book_id
                            AND a.alias = COALESCE(segments.speaker_raw, segments.speaker))
           WHERE book_id = ?
             AND COALESCE(speaker_raw, speaker) IN
                 (SELECT alias FROM speaker_aliases WHERE book_id = ?)""",
        (book_id, book_id),
    )
    changed = conn.total_changes
    conn.commit()
    return changed
