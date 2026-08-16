"""Turn chapter text into attributed segments.

Reuses core's deterministic analysis nodes directly: the splitter,
explicit attribution, turn taking, and the character registry are pure Python
with no GPU and no model server behind them, so a whole book can be segmented
on the workstation. The two Ollama-backed nodes (AI attribution, emotion) are
deliberately left out — they are enrichment passes that run later against the
stored corpus rather than blocking the build.
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from core.analysis.nodes import (  # noqa: E402
    character_registry,
    explicit_attribution,
    segment_splitter,
    turn_taking,
)


def segment_chapter(text: str, heading: str | None = None) -> dict:
    """Split *text* into attributed segments plus a character registry."""
    segments = segment_splitter.split_segments(text)
    segments = explicit_attribution.attribute_explicit(segments)
    segments = turn_taking.apply_turn_taking(segments)
    characters = character_registry.build_character_registry(segments)

    # The chapter title is narrated but is not part of the prose; marking it
    # keeps it out of voice corpora while leaving it in place for alignment.
    if heading:
        for seg in segments[:1]:
            if seg.original_text.strip() == heading.strip():
                seg.kind = "heading"
                seg.speaker = "narrator"

    out = []
    for seg in segments:
        d = asdict(seg)
        d["text"] = d.pop("original_text")
        out.append(d)

    return {"segments": out, "characters": characters}


def run(text_dir: Path, out_dir: Path, numbers: list[int]) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = []
    for n in numbers:
        text = (text_dir / f"ch{n:03d}.txt").read_text(encoding="utf-8")
        meta_path = text_dir / f"ch{n:03d}.json"
        heading = json.loads(meta_path.read_text(encoding="utf-8")).get("heading")

        result = segment_chapter(text, heading)
        (out_dir / f"ch{n:03d}.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        segs = result["segments"]
        summary.append({
            "chapter": n,
            "segments": len(segs),
            "dialogue": sum(1 for s in segs if s["kind"] == "dialogue"),
            "unknown": sum(1 for s in segs if s["speaker"] == "unknown"),
            "characters": len(result["characters"]) - 1,
        })
    return summary
