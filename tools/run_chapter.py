#!/usr/bin/env python3
"""Chapter in, verified MP3 out.

Drives analyze -> synthesize -> QA verify and prints a single report.
Usage: python3 -u run_chapter.py <input.txt> [engine]
"""
import json, sys, time, urllib.request, urllib.error

BASE = "http://localhost:8080"
QA = "http://localhost:8006"
QWEN_VOICES = ["Serena", "Vivian", "Dylan", "Ryan", "Aiden", "Eric", "Sohee", "Ono_Anna", "Uncle_Fu"]


def post(url, payload, timeout=60):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def status(job_id):
    try:
        with urllib.request.urlopen(f"{BASE}/status/{job_id}", timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError:
        return {}


def wait(job_id, want_phase, limit_s, label):
    t0, last = time.time(), ""
    while time.time() - t0 < limit_s:
        s = status(job_id)
        if s.get("status") == "error":
            print(f"  !! {label} ERROR: {s.get('error')}")
            return None
        t = s.get("nodes", {}).get("tts-router", {})
        cur = f"{t.get('completed')}/{t.get('total')}"
        if cur != last and t.get("total"):
            print(f"  … {cur} segments ({time.time()-t0:.0f}s)", flush=True)
            last = cur
        if s.get("phase") == want_phase and s.get("status") == "done":
            return s
        time.sleep(3)
    print(f"  !! {label} timed out after {limit_s}s")
    return None


def main():
    path = sys.argv[1]
    engine = sys.argv[2] if len(sys.argv) > 2 else "qwen3-tts"
    text = open(path, encoding="utf-8").read()
    job = f"run{int(time.time())}"
    print(f"== {path}: {len(text.split())} words | engine={engine} | job={job}", flush=True)

    t0 = time.time()
    post(f"{BASE}/api/analyze", {"job_id": job, "title": "Chapter", "text": text})
    s = wait(job, "analyzing", 1800, "analyze")
    if not s:
        return 1
    t_an = time.time() - t0
    segs = s.get("segments", [])
    chars = s.get("characters", [])
    speakers = sorted({x.get("speaker", "default") for x in segs})
    print(f"== ANALYZE {t_an:.0f}s -> {len(segs)} segments, {len(speakers)} speakers")
    print(f"   speakers: {speakers}")
    for c in chars:
        print(f"   character: {c.get('name')} gender={c.get('gender')} "
              f"segments={c.get('segments')}", flush=True)

    # Voice assignment is left to the orchestrator's gender-aware autocast.
    # Passing a hand-rolled mapping here is what put a female voice on Jason.
    emap = {sp: engine for sp in speakers}

    t1 = time.time()
    post(f"{BASE}/api/synthesize", {"job_id": job, "segments": segs, "characters": chars,
                                    "voice_mapping": {}, "engine_mapping": emap})
    s = wait(job, "done", 10800, "synthesize")
    if not s:
        return 1
    t_syn = time.time() - t1
    out = s.get("output_file")
    print(f"== SYNTH {t_syn:.0f}s -> {out}", flush=True)

    # QA now runs inside the orchestrator, between assembly and cleanup, so the
    # report arrives with the final status rather than being requested here.
    qa = s.get("qa") or {}
    if not qa:
        print("== QA  no report in status (older orchestrator?)")
    elif qa.get("status") in ("skipped", "unavailable"):
        print(f"== QA  {qa['status']}: {qa.get('reason')}")
    else:
        print(f"== QA  {qa['status'].upper()}  checked {qa['checked']}  passed {qa['passed']}"
              f"  failed {qa['failed_count']}  suspect {qa['suspect_count']}"
              f"  mean similarity {qa['mean_similarity']}")
        if qa.get("missing_files"):
            print(f"   missing files: {qa['missing_files'][:10]}")
        for f in qa.get("failed", [])[:10]:
            print(f"   FAIL seg{f['id']:<4} sim={f['similarity']:.2f} ({f['words']}w)")
            print(f"      expected: {f['expected'][:90]}")
            print(f"      heard   : {f['heard'][:90]}")
        for f in qa.get("suspect", [])[:5]:
            print(f"   suspect seg{f['id']:<4} sim={f['similarity']:.2f} ({f['words']}w) "
                  f"exp={f['expected'][:40]!r} heard={f['heard'][:40]!r}")

    print(f"\n== TOTAL {t_an + t_syn:.0f}s  output={out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
