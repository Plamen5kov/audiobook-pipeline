"""Isolate why short segments produce runaway audio (problem #22).

Written on the Spark while chasing the drawl: a four-word line takes 4.3 s of
speech where the professional narrator takes 0.76 s. This talks to the model
directly, with no service layer in the way, so a slow take cannot be blamed on
the router, the queue or the request shape.

Tests the model directly (no service layer): short vs long text, with vs without
instruct, repeated to see if it is deterministic.
"""
import time, numpy as np, torch, soundfile as sf
from qwen_tts import Qwen3TTSModel

MODEL_ID = "Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice"
print("loading model...", flush=True)
m = Qwen3TTSModel.from_pretrained(MODEL_ID, device_map="cuda:0", dtype=torch.bfloat16)
print("loaded", flush=True)

CASES = [
    ("very short", "Bingley."),
    ("short dialogue", "Nonsense, nonsense!"),
    ("dialogue tag", "said Lydia, stoutly,"),
    ("medium", "I hope Mr. Bingley will like it, Lizzy."),
    ("long", "It is a truth universally acknowledged, that a single man in "
             "possession of a good fortune must be in want of a wife."),
]
INSTRUCTS = [None, "calm and measured storytelling voice"]

print(f"\n{'case':<16}{'words':>6}{'instruct':>10}{'dur_s':>9}{'s/word':>9}{'gen_s':>8}")
print("-" * 60)
for label, text in CASES:
    w = len(text.split())
    for ins in INSTRUCTS:
        t0 = time.time()
        wavs, sr = m.generate_custom_voice(text=text, language="English",
                                           speaker="Serena", instruct=ins)
        gen = time.time() - t0
        audio = wavs[0]
        dur = len(audio) / sr
        tag = "yes" if ins else "no"
        print(f"{label:<16}{w:>6}{tag:>10}{dur:>9.1f}{dur/max(w,1):>9.1f}{gen:>8.1f}", flush=True)
        # keep a sample of the worst case for inspection
        if dur / max(w, 1) > 5:
            sf.write(f"/data/intermediate/probe_{label.replace(' ','_')}_{tag}.wav", audio, sr)
