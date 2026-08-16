# Working in this repository

Read this before changing anything. `ARCHITECTURE.md` is the reference for
schemas, ports and past decisions; this file is the map.

## What this is

Two things at once, and the second explains most of the structure.

It is an audiobook pipeline: a chapter of text goes in, an audiobook with a
narrator and a distinct voice per character comes out, using open-source models
on a local GPU.

It is also a studio for that pipeline. Every stage keeps what it produced, so a
person can look at the intermediate results, hear a line, change it and render
only that line. That is deliberate and temporary: the inspection exists so
problems get noticed early, and it should stay useful right up until the
pipeline is reliable enough to run unattended.

## Four codebases, and which way they point

| Directory | What it is | Runs |
|---|---|---|
| `core/` | Domain logic. No HTTP, no web framework, no model transport. | Imported |
| `services/` | Process boundaries: anything owning a port or a model. | Docker Compose |
| `hosted/` | The human loop: React frontend, NestJS gateway. | Docker Compose |
| `corpus/` | Offline CLI pairing book text with professional narration in SQLite. | On the workstation |
| `tools/` | Batch and one-off scripts. | Ad hoc |

Dependencies point one way: `services`, `corpus` and `tools` all import
`core`. Nothing in `core` imports any of them, and no service imports `corpus`
or `tools`.

**The rule that matters:** `core` must stay importable without FastAPI, httpx
or torch. The corpus builder segments a whole book on the workstation and the
offline scripts run without the stack up; both break the moment `core` drags in
a transport. When a piece of `core` needs a client, it takes an interface and
the caller supplies it — see `core/analysis/llm.py`.

## Where things are

```
core/
  analysis/      text → attributed, emotion-tagged, speakable segments
    nodes/       one file per pipeline step, each registering itself
    pipeline.py  the ordered node list, ordering checks, timing
  casting/       who gets which voice
  verification/  scoring a transcription against what was meant to be said
  jobs/          the workspace: stage artifacts, manifest, what needs redoing

services/
  studio-api     :8080  drives runs, serves artifacts, reports progress
  text-analyzer  :8001  thin HTTP wrapper over core.analysis
  tts-router     :8010  hands each request an idle engine replica
  xtts-v2        :8003  and qwen3-tts :8007 — the model runners
  audio-assembly :8005  ffmpeg concatenation
  qa-verifier    :8006  Whisper; scoring comes from core.verification
```

## Adding an analysis step

The pipeline is a registry, not a fixed sequence. Put a module in
`core/analysis/nodes/`, subclass `Node`, decorate it `@register`, declare what
it `requires` and `assigns`, and name it in `DEFAULT_PIPELINE`. Nothing else
changes: the package discovers modules, so no existing file keeps a list.

Declare `requires`/`assigns` honestly. The pipeline checks ordering before it
runs and reports a step that needs something nothing before it produces, but it
believes what a node declares, so a wrong declaration hides a real problem.

## Looking inside a run

Each run writes to `output/workspace/<job-id>/`, numbered in pipeline order:

```
00-input/      chapter.txt
01-analysis/   segments.json     what was meant to be said, and by whom
02-cast/       cast.json         who got which voice
03-synthesis/  0001.wav …        the individual takes
04-assembly/   the finished audio
05-qa/         report.json       what the transcription check flagged
manifest.json  per-stage status, and a fingerprint per segment
```

The same information is on the studio API, which is what the frontend uses:

```
GET  /api/jobs                                    every run, how far it got
GET  /api/jobs/{job}                              stages and what each produced
GET  /api/jobs/{job}/stages/{stage}               that stage's artifact
GET  /api/jobs/{job}/segments[?failed&speaker]    every line, joined with its
                                                  clip state and QA verdict
GET  /api/jobs/{job}/segments/{id}/audio          listen to one take
POST /api/jobs/{job}/redo  {"segments":[12,40]}   render those again next run
```

The segments view joins the analysis, the manifest and the QA report, because
that correlation is the question being asked and doing it in the frontend would
mean three fetches and a join per screen.

```bash
python3 tools/job.py list                  # every run, how far it got
python3 tools/job.py show <job>            # stages and artifacts
python3 tools/job.py segments <job> --failed
python3 tools/job.py redo <job> 12 40      # render those lines again next run
```

Synthesis fingerprints each segment over its text and delivery settings and
renders only what changed, so editing one line costs one line. Reuse also
requires the clip to still exist, and segment audio is kept after assembly
because it is both what gets reused and what you listen to.

## Commands

```bash
docker compose up -d                       # the stack
docker compose up -d --build studio-api    # after changing a service

# core tests (no stack, no GPU)
docker run --rm -v "$PWD":/w -w /w python:3.11-slim \
  sh -c "pip install -q pytest && python -m pytest core -q"

# corpus tests, on the host
cd corpus && python3 -m unittest discover -s tests -q
```

A pre-commit hook runs the corpus tests. The `core` and service suites need
pytest, which the host does not have, so run them in a container as above.

## Conventions

- Services own a port or a model; anything else belongs in `core`.
- `studio-api`, `text-analyzer` and `qa-verifier` build with the **repo root**
  as their Docker context, because they copy `core/`.
- Tests live beside what they test: `core/<area>/tests/`, `services/<name>/tests/`.
- Reference voice clips are in `voices/`; `corpus/voicebank/` holds clips cut
  from the aligned corpus, which is a different thing.
- `corpus/work/` and `data/` hold large derived artifacts and are not in git.
- Conventional commits. No test counts in commit messages.
