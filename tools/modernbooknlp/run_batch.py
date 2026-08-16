"""Run ModernBookNLP over many chapters, loading the model once.

Whole-book input was tried and rejected: it gave no accuracy gain over
per-chapter processing, fragmented characters worse, and a 1.2 MB book was
killed by the OOM killer. Per-chapter keeps peak memory flat and is the mode
that scored best on the hand-checked passage.

Model startup dominates a single chapter, so the loop matters: loading once and
processing 113 chapters is far cheaper than 113 processes.

Emits one combined JSONL of attributions per corpus, which is what the
evaluator consumes.

Usage: run_batch.py <glob> <out_dir> <corpus_id> [joint|direct]
"""

from __future__ import annotations

import csv
import glob
import json
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

from booknlp.english.english_booknlp import EnglishBookNLP


def _read_tsv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


def _labels(entities: list[dict]) -> dict[str, str]:
    by: dict[str, Counter] = defaultdict(Counter)
    for e in entities:
        if e.get("cat") == "PER":
            by[e["COREF"]][(e.get("prop"), e["text"])] += 1
    out: dict[str, str] = {}
    for cid, c in by.items():
        props = Counter({t: n for (p, t), n in c.items() if p == "PROP"})
        noms = Counter({t: n for (p, t), n in c.items() if p == "NOM"})
        if props:
            out[cid] = props.most_common(1)[0][0]
        elif noms:
            out[cid] = "[" + noms.most_common(1)[0][0] + "]"
    return out


def main() -> None:
    pattern, out_root, corpus_id = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
    scoring = sys.argv[4] if len(sys.argv) > 4 else "joint"

    files = sorted(glob.glob(pattern))
    if not files:
        sys.exit(f"no files matched {pattern!r}")
    work = out_root / corpus_id
    work.mkdir(parents=True, exist_ok=True)

    print(f"{corpus_id}: {len(files)} chapters, scoring={scoring}", flush=True)
    booknlp = EnglishBookNLP({
        "pipeline": "entity,quote,coref",
        "model": "big",
        "modern_qa": True,
        "direct_qa": scoring == "direct",
    })

    out_path = out_root / f"{corpus_id}.attributions.jsonl"
    done = failed = quotes_total = named_total = 0
    t0 = time.time()

    with out_path.open("w", encoding="utf-8") as fh:
        for path in files:
            stem = Path(path).stem
            try:
                booknlp.process(path, str(work), stem)
            except Exception as exc:
                failed += 1
                print(f"  {stem}: FAILED {type(exc).__name__}: {exc}"[:200],
                      flush=True)
                continue

            quotes = _read_tsv(work / f"{stem}.quotes")
            label = _labels(_read_tsv(work / f"{stem}.entities"))
            for q in quotes:
                speaker = label.get(q.get("char_id"))
                quotes_total += 1
                named_total += bool(speaker)
                fh.write(json.dumps({
                    "chapter": stem,
                    "speaker": speaker,
                    "mention": q.get("mention_phrase"),
                    "quote": q.get("quote", ""),
                }, ensure_ascii=False) + "\n")
            done += 1
            if done % 10 == 0:
                rate = (time.time() - t0) / done
                left = rate * (len(files) - done) / 60
                print(f"  {done}/{len(files)} chapters, {quotes_total:,} quotes, "
                      f"~{left:.0f} min left", flush=True)

    print(f"\n{corpus_id}: {done} chapters ok, {failed} failed, "
          f"{quotes_total:,} quotes, {named_total:,} named "
          f"({100 * named_total / max(quotes_total, 1):.1f}%), "
          f"{(time.time() - t0) / 60:.1f} min -> {out_path}", flush=True)


if __name__ == "__main__":
    main()
