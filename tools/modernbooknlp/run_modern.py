"""Run ModernBookNLP over a text and emit speaker attributions per quote.

Same output shape as tools/booknlp/run_booknlp.py so the two can be scored with
the same evaluator. The only difference is the ``modern_qa`` flag, which swaps
the vanilla quotation-attribution model for the fork's joint-scoring one.

Usage: run_modern.py <input.txt> <out_dir> <book_id> [joint|direct]
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

from booknlp.english.english_booknlp import EnglishBookNLP


def _read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def main() -> None:
    src = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    book_id = sys.argv[3]
    scoring = sys.argv[4] if len(sys.argv) > 4 else "joint"

    out_dir.mkdir(parents=True, exist_ok=True)
    booknlp = EnglishBookNLP({
        "pipeline": "entity,quote,coref",
        "model": "big",
        "modern_qa": True,
        "direct_qa": scoring == "direct",
    })
    booknlp.process(str(src), str(out_dir), book_id)

    quotes = _read_tsv(out_dir / f"{book_id}.quotes")
    entities = _read_tsv(out_dir / f"{book_id}.entities")

    by: dict[str, Counter] = defaultdict(Counter)
    for e in entities:
        if e.get("cat") == "PER":
            by[e["COREF"]][(e.get("prop"), e["text"])] += 1

    # Prefer a proper name, fall back to a nominal. A character can go a whole
    # chapter unnamed, and dropping those clusters loses real attributions.
    label: dict[str, str] = {}
    for cid, c in by.items():
        props = Counter({t: n for (p, t), n in c.items() if p == "PROP"})
        noms = Counter({t: n for (p, t), n in c.items() if p == "NOM"})
        if props:
            label[cid] = props.most_common(1)[0][0]
        elif noms:
            label[cid] = "[" + noms.most_common(1)[0][0] + "]"

    resolved = [{
        "quote_start": int(q["quote_start"]),
        "char_id": q.get("char_id"),
        "speaker": label.get(q.get("char_id")),
        "mention": q.get("mention_phrase"),
        "quote": q.get("quote", "")[:300],
    } for q in quotes]

    (out_dir / f"{book_id}.speakers.json").write_text(
        json.dumps({"scoring": scoring, "characters": label,
                    "quotes": resolved}, ensure_ascii=False, indent=1),
        encoding="utf-8")

    named = sum(1 for r in resolved if r["speaker"])
    print(f"scoring={scoring} quotes={len(resolved)} named={named} "
          f"({100 * named / max(len(resolved), 1):.1f}%) clusters={len(label)}")


if __name__ == "__main__":
    main()
