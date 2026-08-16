"""Score attributions against the quotes a novel tags explicitly.

Ground truth is the set of quotes whose speaker the text names outright. Those
are unambiguous, and the rule-based pass already isolated them, so thousands of
labelled cases come for free.

The caveat is unchanged and worth repeating: these are the *easy* cases by
construction. A good score is a floor. Long untagged exchanges, where the
interesting failures live, are not represented here at all.

Usage: eval_attributions_jsonl.py <corpus.db> <book_id> <attributions.jsonl>
"""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORD = re.compile(r"[a-z']+")


def norm(text: str) -> str:
    return " ".join(WORD.findall((text or "").lower()))


def main() -> None:
    db_path, book_id, jsonl = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    truth: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute(
        """SELECT text, speaker FROM segments
           WHERE book_id = ? AND kind = 'dialogue'
             AND attribution_source = 'explicit' AND speaker != 'unknown'""",
            (book_id,)):
        key = norm(r["text"])
        if len(key.split()) >= 4:
            truth[key][r["speaker"]] += 1
    gold = {k: c.most_common(1)[0][0] for k, c in truth.items() if len(c) == 1}

    # Aliases the corpus already merged, so a prediction of a surname is not
    # scored as a miss when the corpus canonicalised it to a first name.
    alias = {a.lower(): c.lower() for a, c in conn.execute(
        "SELECT alias, canonical FROM speaker_aliases WHERE book_id = ?",
        (book_id,))}

    matched = correct = unnamed = 0
    confusion: Counter = Counter()
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            key = norm(rec.get("quote", ""))
            if key not in gold:
                continue
            matched += 1
            got = (rec.get("speaker") or "").strip("[]")
            if not got:
                unnamed += 1
                continue
            want = gold[key]
            g, w = got.lower(), want.lower()
            g = alias.get(g, g)
            if g == w or g in w or w in g:
                correct += 1
            else:
                confusion[(want, got)] += 1

    print(f"ground truth        : {len(gold):,} unambiguous explicit quotes")
    print(f"matched quotes      : {matched:,}")
    if matched:
        print(f"correct             : {correct:,} ({100 * correct / matched:.1f}%)")
        print(f"no name             : {unnamed:,} ({100 * unnamed / matched:.1f}%)")
        wrong = matched - correct - unnamed
        print(f"wrong name          : {wrong:,} ({100 * wrong / matched:.1f}%)")
    print("\nmost common confusions (truth -> predicted):")
    for (want, got), n in confusion.most_common(10):
        print(f"  {want:<14} -> {got:<20} {n}")


if __name__ == "__main__":
    main()
