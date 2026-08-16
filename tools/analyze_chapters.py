"""Run chapter text through the full text-analyzer pipeline.

The corpus builder deliberately runs only the deterministic nodes, because a
whole book does not need an LLM to be segmented. New chapters do: they are
dialogue-dense and the rule-based turn taking leaves far more unattributed
than it does on a novel's prose.

Usage: python3 analyze_chapters.py <out_dir> <chapter.txt> [more.txt ...]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

ANALYZER = "http://localhost:8001/analyze"


def analyze(path: Path, out_dir: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    payload = json.dumps({"text": text, "title": path.stem}).encode("utf-8")
    req = urllib.request.Request(
        ANALYZER, data=payload, headers={"Content-Type": "application/json"})

    t0 = time.time()
    with urllib.request.urlopen(req, timeout=3600) as resp:
        result = json.load(resp)
    elapsed = time.time() - t0

    out = out_dir / f"{path.stem}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                   encoding="utf-8")

    segs = result.get("segments", [])
    unknown = sum(1 for s in segs if s.get("speaker") == "unknown")
    speakers = {s.get("speaker") for s in segs} - {"unknown"}
    print(f"{path.name}: {len(segs)} segments, {unknown} unattributed "
          f"({100 * unknown / max(len(segs), 1):.1f}%), "
          f"{len(speakers)} speakers, {elapsed:.0f}s", flush=True)
    return result


def main() -> None:
    out_dir = Path(sys.argv[1])
    out_dir.mkdir(parents=True, exist_ok=True)
    for arg in sys.argv[2:]:
        try:
            analyze(Path(arg), out_dir)
        except Exception as exc:
            print(f"{arg}: FAILED {exc}", flush=True)


if __name__ == "__main__":
    main()
