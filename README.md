# Audiobook Pipeline

Turn book chapters into full audiobooks with **distinct voices per character** and **emotional delivery**, using open-source models running locally on GPU.

## What it does

Paste a chapter of text and the pipeline will:

1. **Analyze** the text: split into segments, attribute speakers, classify emotions (hybrid: deterministic parsing + targeted LLM calls)
2. **Cast voices**: assign TTS engines and voice profiles per character via YAML config
3. **Synthesize** speech: parallel TTS generation through a router supporting multiple engines
4. **Assemble** the final audio: concatenate, normalize volume, insert pauses, crossfade
5. **QA verify** (Phase 2): Whisper-based transcription comparison to catch synthesis errors

## Architecture

```
  React Frontend  ──▶  NestJS Backend  ──▶  File Server (orchestrator)
                                                    │
                    ┌───────────┬───────────┬───────┘
                    ▼           ▼           ▼
              Text Analyzer  TTS Router  Audio Assembly
              (hybrid LLM)      │
                          ┌─────┴─────┐
                          ▼           ▼
                       XTTS v2   Qwen3-TTS
                        (GPU)      (GPU)
```

8-container Docker Compose stack. Each service exposes a FastAPI HTTP API.

## TTS Engines

| Engine | Mechanism | Voice control |
|---|---|---|
| XTTS v2 | Voice cloning from reference WAV | One WAV file per character |
| Qwen3-TTS | 9 predefined voices + instruction control | Natural language emotion prompts |

Adding a new engine: one Docker service + one entry in `TTS_BACKENDS` env var. Zero code changes.

## Text Analyzer

10-node hybrid pipeline (8 deterministic + 2 AI):

- **Deterministic**: segment splitting (state machine), speaker attribution (regex), turn-taking heuristic, character registry, pause timing, validation, narration defaults, text normalisation
- **AI** (Ollama): ambiguous speaker resolution, emotion classification

Programmatic nodes run in ~50ms. AI nodes take 5-20s depending on chapter length.

The pipeline is a list of nodes, not a fixed sequence. Every node reads and writes one shared
`AnalysisContext`, so a new step can be inserted anywhere without changing another node's
signature. Nodes declare what they `requires` and `assigns`, and the pipeline checks that
ordering before it runs rather than failing part-way through a chapter. Dropping a module into
`app/nodes/` registers it; nothing keeps a list of modules in step.

Nodes that need a language model receive an `LLMClient`, not a URL, so the whole pipeline runs
in tests against a scripted fake with no Ollama and no network.

Each segment carries two texts. `original_text` is the prose, verbatim, which is what the
validation node proves still reconstructs the source. `spoken_text` is what the synthesiser is
actually given: scene breaks removed and turned into silence, bracket tags unwrapped, stat-block
debris cleared, ratios read as words. QA compares the transcript against the spoken form, since
that is what was meant to be said.

Pronunciation that no rule can derive goes in a per-book lexicon on the context
(`AnalysisContext.lexicon`), a plain term-to-spoken-form mapping. Acronyms live there rather than
in a pattern: these chapters contain `CIA` and `EDJI`, which want spelling out, beside `THAT` for
emphasis and `II` as a numeral, which do not.

## Tech Stack

- **Frontend**: React, Vite, TypeScript
- **Backend**: NestJS (API gateway with helmet, rate limiting, path traversal protection)
- **Pipeline services**: Python, FastAPI, asyncio
- **TTS**: XTTS v2, Qwen3-TTS-12Hz-1.7B
- **LLM**: Ollama (shared instance)
- **Audio**: ffmpeg, pydub
- **Infrastructure**: Docker Compose, NVIDIA DGX Spark (128GB unified memory)

## Quick Start

```bash
# Clone and start all services
docker compose up -d

# Wait for models to load (XTTS and Qwen3-TTS need ~2min on first start)
docker compose logs -f

# Open the web UI
# Frontend runs on the NestJS backend port
```

## Voice Casting

Edit `voice-cast.yaml` to map characters to voices and engines:

```yaml
voices:
  narrator:
    tts_service: xtts-v2
    reference_audio: /voices/narrator.wav
    speed: 0.95
  Elena:
    tts_service: qwen3-tts
    qwen_speaker: Vivian
    qwen_instruct: young energetic female voice
```

## Project Structure

```
audiobook-pipeline/
  core/                Domain logic, no HTTP and no framework
    analysis/          Text into attributed, speakable segments
    casting/           Who gets which voice
    verification/      Scoring a transcription against the intended text
    jobs/              Job workspace: stage artifacts and what needs redoing
  hosted/
    frontend/          React + Vite PWA
    backend/           NestJS API gateway
  services/
    text-analyzer/     HTTP wrapper over core.analysis
    studio-api/        Drives runs, serves artifacts, reports progress
    xtts-v2/           XTTS v2 TTS service
    qwen3-tts/         Qwen3-TTS service
    tts-router/        Engine routing proxy
    audio-assembly/    ffmpeg-based audio concatenation
    qa-verifier/       Whisper-based QA (Phase 2)
  output/workspace/    Per-run artifacts, one directory per stage
  voices/              Reference audio clips
  voice-cast.yaml      Character-to-voice mapping
  docker-compose.yml
```

## Looking inside a run

Every stage keeps what it produced, under `output/workspace/<job>/`, in
directories numbered in pipeline order. Synthesis fingerprints each segment, so
changing one line renders one line rather than the chapter.

```bash
python3 tools/job.py list
python3 tools/job.py show <job>
python3 tools/job.py segments <job> --failed
python3 tools/job.py redo <job> 12 40
```

## License

Open source. All models are open-source with no paid API dependencies.
