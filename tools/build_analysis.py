"""Build a chapter analysis file using ModernBookNLP's speaker attributions.

Our splitter defines the segments (it guarantees verbatim text and drives
pauses); ModernBookNLP decides who speaks each line. Matching is on normalised
quote text, since the two tools tokenise differently.

Cluster labels that are a nominal phrase rather than a name (a character the
chapter never names) are mapped through --alias, because the voice bank is
keyed by character name.

Usage: build_analysis.py <chapter.txt> <attributions.jsonl> <out.json>
                        [--alias "his sister=Erika" ...]
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "services" / "text-analyzer"))

from app.nodes import (  # noqa: E402
    explicit_attribution, normalisation, pause_timing, segment_splitter,
    turn_taking,
)

WORD = re.compile(r"[a-z']+")


def norm(text: str) -> str:
    return " ".join(WORD.findall((text or "").lower()))


def main() -> None:
    chapter, jsonl, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    aliases = {}
    for arg in sys.argv[4:]:
        if arg.startswith("--alias"):
            continue
        if "=" in arg:
            k, v = arg.split("=", 1)
            aliases[k.strip().lower()] = v.strip()

    stem = chapter.stem
    attributions = {}
    with jsonl.open(encoding="utf-8") as fh:
        for line in fh:
            rec = json.loads(line)
            if rec.get("chapter") != stem:
                continue
            key = norm(rec.get("quote", ""))
            if key:
                attributions[key] = rec.get("speaker")

    text = chapter.read_text(encoding="utf-8")
    segments = segment_splitter.split_segments(text)
    segments = explicit_attribution.attribute_explicit(segments)
    segments = turn_taking.apply_turn_taking(segments)
    segments = pause_timing.assign_pauses(segments)
    # After the pauses, because dropping a scene-break marker has to leave the
    # beat behind as silence.
    segments = normalisation.normalise_segments(segments)

    # The two tools disagree on where a quote ends when one is split across
    # paragraphs, so an exact match misses those. Falling back to the opening
    # words recovers them without risking a wrong pairing, since six words of
    # dialogue are effectively unique within a chapter.
    PREFIX_WORDS = 6
    prefix_index: dict[str, str] = {}
    for key, speaker in attributions.items():
        words = key.split()
        if len(words) >= PREFIX_WORDS:
            prefix_index.setdefault(" ".join(words[:PREFIX_WORDS]), speaker)

    stats = Counter()
    unmapped = Counter()
    for seg in segments:
        if seg.kind != "dialogue":
            continue
        key = norm(seg.original_text)
        raw = attributions.get(key)
        if not raw:
            words = key.split()
            if len(words) >= PREFIX_WORDS:
                raw = prefix_index.get(" ".join(words[:PREFIX_WORDS]))
            if raw:
                stats["prefix_match"] += 1
        if not raw:
            stats["no_match"] += 1
            continue
        name = raw.strip("[]")
        name = aliases.get(name.lower(), name)
        if name.startswith("[") or name != raw.strip("[]") or True:
            pass
        if raw.startswith("[") and raw.strip("[]").lower() not in aliases:
            unmapped[raw] += 1
        seg.speaker = name
        seg.attribution_source = "modernbooknlp"
        stats["attributed"] += 1

    out = []
    for seg in segments:
        d = asdict(seg)
        d["original_text"] = d.get("original_text", "")
        out.append({
            "id": d["id"], "kind": d["kind"],
            "speaker": d["speaker"] if d["kind"] == "dialogue" else "narrator",
            "original_text": d["original_text"],
            "spoken_text": d.get("spoken_text") or d["original_text"],
            "emotion": "neutral", "intensity": 0.5,
            "pause_before_ms": d.get("pause_before_ms", 0),
        })

    cast = Counter(s["speaker"] for s in out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({"title": stem, "segments": out},
                                   ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"{stem}: {len(out)} segments, {stats['attributed']} attributed by "
          f"ModernBookNLP, {stats['no_match']} unmatched")
    print("cast:", dict(cast.most_common()))
    if unmapped:
        print("UNMAPPED nominal labels (need --alias):", dict(unmapped))


if __name__ == "__main__":
    main()
