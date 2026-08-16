"""Run BookNLP over a chapter and emit speaker attributions per quote.

BookNLP does the two things our hand-rolled rules kept getting wrong: it
clusters the surface forms a character is referred to into one entity, and it
infers referential gender from the pronouns actually used for them. Quotes are
then attached to a character id rather than to whatever name happened to be
nearest.

Usage: run_booknlp.py <input.txt> <out_dir> <book_id> [model]
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from booknlp.booknlp import BookNLP


def _read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def main() -> None:
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    book_id = sys.argv[3]
    model = sys.argv[4] if len(sys.argv) > 4 else "small"

    out_dir.mkdir(parents=True, exist_ok=True)
    booknlp = BookNLP("en", {"pipeline": "entity,quote,coref", "model": model})
    booknlp.process(str(src), str(out_dir), book_id)

    quotes = _read_tsv(out_dir / f"{book_id}.quotes")
    entities = _read_tsv(out_dir / f"{book_id}.entities")

    # Canonical name per character id: the most frequent proper-name mention
    # in that cluster, which is how BookNLP's own reports pick a label.
    names: dict[str, Counter] = defaultdict(Counter)
    for e in entities:
        if e.get("cat") == "PER" and e.get("prop") == "PROP":
            names[e["COREF"]][e["text"]] += 1
    canonical = {cid: c.most_common(1)[0][0] for cid, c in names.items() if c}

    resolved = []
    for q in quotes:
        cid = q.get("char_id")
        resolved.append({
            "quote_start": int(q["quote_start"]),
            "quote_end": int(q["quote_end"]),
            "char_id": cid,
            "speaker": canonical.get(cid),
            "mention": q.get("mention_phrase"),
            "quote": q.get("quote", "")[:300],
        })

    (out_dir / f"{book_id}.speakers.json").write_text(
        json.dumps({"characters": canonical, "quotes": resolved},
                   ensure_ascii=False, indent=1), encoding="utf-8")

    named = sum(1 for r in resolved if r["speaker"])
    print(f"quotes: {len(resolved)}, with a resolved name: {named} "
          f"({100 * named / max(len(resolved), 1):.1f}%)")
    print(f"characters: {len(canonical)}")
    for cid, name in sorted(canonical.items(), key=lambda kv: -names[kv[0]].total())[:12]:
        print(f"  {name:<16} id={cid} mentions={names[cid].total()}")


if __name__ == "__main__":
    main()
