"""Label a character's reference clips with the emotion of what they say.

The cloning checkpoint accepts no emotion instruction, so delivery cannot be
asked for — but a clone inherits the delivery of its reference. Labelling the
corpus clips by emotion turns that into a lever: an angry line can be cloned
from a reference the narrator actually performed angrily.

The label comes from the clip's text rather than its audio. That is a proxy,
and an imperfect one, but the narrator's delivery follows the text closely
enough to be useful, and text is what we can classify cheaply.

Usage: classify_clip_emotions.py <clips.json> <out.json> [ollama_url] [model]
"""

from __future__ import annotations

import json
import sys
import time
import urllib.request
from collections import Counter
from pathlib import Path

ALLOWED = ["neutral", "happy", "sad", "angry", "fearful", "excited",
           "tense", "contemplative", "curious"]
BATCH = 25

SYSTEM = (
    "You label the emotion a line of dialogue is delivered with, for an "
    "audiobook. Use ONLY these labels: " + ", ".join(ALLOWED) + ". "
    "Most ordinary conversation is neutral — do not reach for a strong label "
    "unless the line clearly carries it. Return ONLY valid JSON."
)


def call_ollama(url: str, model: str, prompt: str) -> dict:
    body = json.dumps({
        "model": model,
        "system": SYSTEM,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0},
    }).encode("utf-8")
    req = urllib.request.Request(f"{url}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        outer = json.load(resp)
    return json.loads(outer.get("response", "{}"))


def main() -> None:
    clips = json.loads(Path(sys.argv[1]).read_text())
    out_path = Path(sys.argv[2])
    url = sys.argv[3] if len(sys.argv) > 3 else "http://localhost:11435"
    model = sys.argv[4] if len(sys.argv) > 4 else "qwen2.5:7b"

    print(f"classifying {len(clips)} clips in batches of {BATCH}", flush=True)
    labels: dict[str, dict] = {}
    t0 = time.time()

    for start in range(0, len(clips), BATCH):
        batch = clips[start:start + BATCH]
        listing = "\n".join(
            f'{c["id"]}: {c["text"][:220]}' for c in batch)
        prompt = (
            f"Label the emotion of each line.\n\n{listing}\n\n"
            'Return ONLY: {"emotions": [{"id": N, "emotion": "value", '
            '"intensity": 0.5}, ...]} with one entry per id.'
        )
        try:
            parsed = call_ollama(url, model, prompt)
        except Exception as exc:
            print(f"  batch at {start}: FAILED {exc}"[:160], flush=True)
            continue

        for item in parsed.get("emotions", []):
            emotion = str(item.get("emotion", "")).lower().strip()
            if emotion not in ALLOWED:
                emotion = "neutral"
            labels[str(item.get("id"))] = {
                "emotion": emotion,
                "intensity": float(item.get("intensity") or 0.5),
            }
        done = min(start + BATCH, len(clips))
        if done % 200 < BATCH:
            rate = (time.time() - t0) / max(done, 1)
            print(f"  {done}/{len(clips)}, ~{rate * (len(clips) - done) / 60:.0f} "
                  f"min left", flush=True)

    for c in clips:
        c.update(labels.get(str(c["id"]), {"emotion": "neutral",
                                           "intensity": 0.5}))
    out_path.write_text(json.dumps(clips, ensure_ascii=False, indent=1),
                        encoding="utf-8")

    dist = Counter(c["emotion"] for c in clips)
    print(f"\nlabelled {len(labels)}/{len(clips)} in "
          f"{(time.time() - t0) / 60:.1f} min -> {out_path}")
    for emotion, n in dist.most_common():
        print(f"  {emotion:<16} {n}")


if __name__ == "__main__":
    main()
