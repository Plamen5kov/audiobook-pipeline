#!/usr/bin/env python3
"""End-to-end chapter benchmark for the audiobook pipeline.

Runs analyze -> synthesize against studio-api and reports where the time goes.
Complements tools/bench_tts.py, which measures the router alone.
Usage: python3 bench_chapter.py <input.txt> [engine]
"""
import json, sys, time, urllib.request, urllib.error

BASE = "http://localhost:8080"
QWEN_VOICES = ["Serena", "Vivian", "Dylan", "Ryan", "Aiden", "Eric", "Sohee", "Ono_Anna", "Uncle_Fu"]


def post(path, payload):
    req = urllib.request.Request(
        BASE + path, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def status(job_id):
    try:
        with urllib.request.urlopen(f"{BASE}/status/{job_id}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError:
        return {}


def wait(job_id, want_phase, limit_s, label):
    t0 = time.time()
    last = ""
    while time.time() - t0 < limit_s:
        s = status(job_id)
        st, ph = s.get("status"), s.get("phase")
        if st == "error":
            print(f"  !! {label} ERROR: {s.get('error')}")
            return None
        tts = s.get("nodes", {}).get("tts-router", {})
        cur = f"{tts.get('completed','')}/{tts.get('total','')}"
        if cur != last and tts.get("total"):
            el = time.time() - t0
            print(f"  … {cur} segments  ({el:.0f}s elapsed)", flush=True)
            last = cur
        if ph == want_phase and st == "done":
            return s
        time.sleep(3)
    print(f"  !! {label} TIMED OUT after {limit_s}s")
    return None


def main():
    path = sys.argv[1]
    engine = sys.argv[2] if len(sys.argv) > 2 else "qwen3-tts"
    text = open(path, encoding="utf-8").read()
    words = len(text.split())
    job = f"bench{int(time.time())}"
    print(f"== chapter: {words} words, {len(text)} chars | engine={engine} | job={job}")

    t0 = time.time()
    post("/api/analyze", {"job_id": job, "title": "Benchmark Chapter", "text": text})
    s = wait(job, "analyzing", 900, "analyze")
    if not s:
        return 1
    t_analyze = time.time() - t0
    segs = s.get("segments", [])
    speakers = sorted({x.get("speaker", "default") for x in segs})
    print(f"== ANALYZE {t_analyze:.1f}s -> {len(segs)} segments, {len(speakers)} speakers: {speakers}")

    if engine == "qwen3-tts":
        vmap = {sp: QWEN_VOICES[i % len(QWEN_VOICES)] for i, sp in enumerate(speakers)}
    else:
        vmap = {sp: "generic_neutral.wav" for sp in speakers}
    emap = {sp: engine for sp in speakers}

    t1 = time.time()
    post("/api/synthesize", {"job_id": job, "segments": segs,
                             "voice_mapping": vmap, "engine_mapping": emap})
    s = wait(job, "done", 7200, "synthesize")
    if not s:
        return 1
    t_synth = time.time() - t1

    n = s.get("nodes", {})
    tts, aa = n.get("tts-router", {}), n.get("audio-assembly", {})
    tts_s = (tts.get("finished", 0) - tts.get("started", 0)) or 0
    aa_s = (aa.get("finished", 0) - aa.get("started", 0)) or 0
    print("== RESULT")
    print(f"   analyze     {t_analyze:8.1f}s")
    print(f"   tts         {tts_s:8.1f}s   ({len(segs)} segments)")
    print(f"   assembly    {aa_s:8.1f}s")
    print(f"   synth total {t_synth:8.1f}s")
    print(f"   WALL        {t_analyze + t_synth:8.1f}s")
    print(f"   output      {s.get('output_file')}")
    if segs:
        print(f"   per-segment {tts_s / len(segs):.2f}s avg")
    return 0


if __name__ == "__main__":
    sys.exit(main())
