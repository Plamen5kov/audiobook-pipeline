"""Map a chapter's speakers onto voices from the corpus voice bank.

A character who exists in the corpus keeps their own voice. Everyone else gets
one of the spare bank voices, chosen by a hash of their name rather than at
random, so the same character is given the same voice on every run. A book
whose secondary characters change voice between regenerations would be worse
than one whose casting is arbitrary but stable.

Usage: build_cast.py <bank_dir> <out_dir> <analysis.json> [more.json ...]
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

# Kept aside so no secondary character can be handed the narrator's voice or
# the protagonist's.
PROTECTED = {"narrator", "Jason", "unknown", "default"}


def _stable_index(name: str, count: int) -> int:
    digest = hashlib.sha256(name.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % count


def assign(speakers, bank_voices, forced: dict[str, str] | None = None) -> dict[str, str]:
    """Map each speaker to a bank voice.

    A speaker the corpus already knows keeps their own voice. The rest are
    spread over the spare voices from a hashed starting point, walking forward
    on collision so two characters cannot share a voice while spares remain.
    """
    forced = {k: v for k, v in (forced or {}).items() if v in set(bank_voices)}
    speakers = sorted(set(speakers) - {"unknown"})
    bank = set(bank_voices)

    matched = [s for s in speakers if s in bank and s not in forced]
    unmatched = [s for s in speakers if s not in bank and s not in forced]
    spare = sorted(bank - PROTECTED - set(matched) - set(forced.values()))
    if unmatched and not spare:
        raise ValueError("voice bank has no spare voices to assign")

    # Hashed assignment is gender-blind, which is fine for a walk-on but wrong
    # for anyone the listener will notice. Explicit choices win.
    assignment = {s: s for s in matched}
    assignment.update({s: v for s, v in forced.items() if s in speakers})
    taken: set[str] = set(forced.values())
    for speaker in unmatched:
        start = _stable_index(speaker, len(spare))
        choice = spare[start]
        for offset in range(len(spare)):
            candidate = spare[(start + offset) % len(spare)]
            if candidate not in taken:
                choice = candidate
                break
        taken.add(choice)
        assignment[speaker] = choice
    return assignment


def main() -> None:
    bank_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    # Override args (Name=Voice) share the tail with analysis paths.
    analyses = [Path(a) for a in sys.argv[3:] if "=" not in a]

    bank = json.loads((bank_dir / "voicebank.json").read_text())["voices"]

    speakers: set[str] = set()
    for path in analyses:
        data = json.loads(path.read_text())
        speakers.update(s.get("speaker", "unknown") for s in data.get("segments", []))
    speakers.discard("unknown")

    forced = {}
    for arg in sys.argv[4:]:
        if "=" in arg:
            k, v = arg.split("=", 1)
            forced[k.strip()] = v.strip()
    try:
        assignment = assign(speakers, bank.keys(), forced)
    except ValueError as exc:
        sys.exit(str(exc))

    out_dir.mkdir(parents=True, exist_ok=True)
    cast: dict[str, dict] = {}

    # A reference chosen by ear beats one chosen by alignment score. Pins are
    # kept outside the bank so re-exporting the bank cannot silently drop them.
    pins_dir = bank_dir.parent / "pins"
    pins_file = pins_dir / "pins.json"
    pins = json.loads(pins_file.read_text()) if pins_file.exists() else {}

    # A preset voice lives in a different checkpoint and has no reference clip,
    # so it is recorded as a routing decision rather than as a file.
    engines: dict[str, dict] = {}

    for speaker, voice in assignment.items():
        pin = pins.get(speaker)
        if pin and pin.get("engine") == "qwen3-preset":
            engines[speaker] = {"engine": "qwen3-preset",
                                "qwen_speaker": pin["qwen_speaker"]}
            cast[speaker] = {"preset": pin["qwen_speaker"],
                             "engine": "qwen3-preset",
                             "source_voice": "preset:" + pin["qwen_speaker"],
                             "own_voice": False}
            continue
        if pin:
            best = pin
            src = pins_dir / best["file"]
        else:
            clips = bank[voice]
            best = max(clips, key=lambda c: (c.get("align_score") or 0))
            src = bank_dir / best["file"]
        dest_name = f"{speaker.replace(' ', '_')}.wav"
        shutil.copy2(src, out_dir / dest_name)
        cast[speaker] = {
            "file": dest_name,
            "text": best["text"],
            "align_score": best["align_score"],
            "duration_s": best["duration_s"],
            "source_voice": voice,
            "own_voice": voice == speaker,
        }

    (out_dir / "voicebank.json").write_text(json.dumps(
        {"voices": {k: [v] for k, v in cast.items() if "file" in v}},
        ensure_ascii=False, indent=1), encoding="utf-8")

    # Read by the production driver to route each character to the checkpoint
    # that can actually produce their voice.
    (out_dir / "cast.json").write_text(json.dumps(engines, ensure_ascii=False,
                                                  indent=1), encoding="utf-8")

    print(f"{len(cast)} voices -> {out_dir}")
    for speaker in sorted(cast, key=lambda s: (not cast[s]["own_voice"], s)):
        c = cast[speaker]
        tag = ("own voice" if c["own_voice"]
               else f"borrowed: {c['source_voice']}")
        print(f"  {speaker:<12} {tag}")


if __name__ == "__main__":
    main()
