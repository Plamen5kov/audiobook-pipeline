"""Score BookNLP's speaker attribution against explicitly-tagged quotes.

Ground truth comes from the quotes a novel tags outright ("...", said Rufus).
The rule-based pass already isolated those, and they are unambiguous, which
gives thousands of labelled cases without anyone annotating anything.

The obvious caveat: explicitly tagged lines are the easy ones by construction.
A good score here is a floor, not proof, and says little about long untagged
exchanges — which is exactly where both approaches are known to struggle. Read
it as "does this get the easy cases right", not "is this solved".

Usage: eval_attribution.py <corpus.db> <book_id> <booknlp_prefix>
"""

from __future__ import annotations

import csv
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

WORD = re.compile(r"[a-z']+")


def norm(text: str) -> str:
    return " ".join(WORD.findall((text or "").lower()))


def load_booknlp(prefix: Path) -> tuple[list[dict], dict[str, str]]:
    with (prefix.with_suffix(".quotes")).open(encoding="utf-8") as fh:
        quotes = list(csv.DictReader(fh, delimiter="\t"))
    with (prefix.with_suffix(".entities")).open(encoding="utf-8") as fh:
        ents = list(csv.DictReader(fh, delimiter="\t"))

    by: dict[str, Counter] = defaultdict(Counter)
    for e in ents:
        if e.get("cat") == "PER":
            by[e["COREF"]][(e.get("prop"), e["text"])] += 1

    # Label a cluster by its most common proper name, falling back to its most
    # common nominal. The fallback matters: a character can go a whole chapter
    # without being named, and dropping those clusters was what made an earlier
    # evaluation of mine look catastrophic when it was merely incomplete.
    label: dict[str, str] = {}
    for cid, c in by.items():
        props = Counter({t: n for (p, t), n in c.items() if p == "PROP"})
        noms = Counter({t: n for (p, t), n in c.items() if p == "NOM"})
        if props:
            label[cid] = props.most_common(1)[0][0]
        elif noms:
            label[cid] = "[" + noms.most_common(1)[0][0] + "]"
    return quotes, label


def main() -> None:
    db_path, book_id, prefix = sys.argv[1], int(sys.argv[2]), Path(sys.argv[3])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    truth: dict[str, Counter] = defaultdict(Counter)
    for r in conn.execute(
        """SELECT text, speaker FROM segments
           WHERE book_id = ? AND kind = 'dialogue'
             AND attribution_source = 'explicit' AND speaker != 'unknown'""",
            (book_id,)):
        key = norm(r["text"])
        if len(key.split()) >= 4:      # short lines collide across a novel
            truth[key][r["speaker"]] += 1

    # A line of dialogue can recur with different speakers; keep only the ones
    # whose attribution is unambiguous so the score measures the model, not the
    # ambiguity of the key.
    gold = {k: c.most_common(1)[0][0] for k, c in truth.items() if len(c) == 1}
    print(f"ground truth: {len(gold):,} unambiguous explicit quotes")

    quotes, label = load_booknlp(prefix)
    print(f"booknlp: {len(quotes):,} quotes, {len(label):,} labelled clusters")

    matched = correct = unlabelled = 0
    confusion: Counter = Counter()
    for q in quotes:
        key = norm(q.get("quote", ""))
        if key not in gold:
            continue
        matched += 1
        got = label.get(q.get("char_id"))
        if not got:
            unlabelled += 1
            continue
        want = gold[key]
        # BookNLP labels a cluster with one surface form; the corpus uses its
        # own canonical name. Count a hit when either contains the other, so
        # "Rufus" and "Rufus Remore" are not scored as a miss.
        g, w = got.strip("[]").lower(), want.lower()
        if g == w or g in w or w in g:
            correct += 1
        else:
            confusion[(want, got)] += 1

    print()
    print(f"matched quotes      : {matched:,}")
    if matched:
        print(f"correct             : {correct:,} ({100 * correct / matched:.1f}%)")
        print(f"no cluster label    : {unlabelled:,} ({100 * unlabelled / matched:.1f}%)")
        wrong = matched - correct - unlabelled
        print(f"wrong name          : {wrong:,} ({100 * wrong / matched:.1f}%)")
    print()
    print("most common confusions (truth -> booknlp):")
    for (want, got), n in confusion.most_common(12):
        print(f"  {want:<14} -> {got:<20} {n}")


if __name__ == "__main__":
    main()
