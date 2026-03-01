# Audiobook Pipeline — Architecture & Plan

> **Source of truth** for the project's design, decisions, and roadmap.
> Update this document as the architecture evolves. Keep a changelog at the bottom.

---

## 1. Vision

Turn a book chapter (plain text) into a full audiobook with **distinct voices per character**, a dedicated **narrator voice**, and appropriate **emotional delivery** — all using open-source models running locally on an NVIDIA DGX Spark.

### Core Principles

- **Open-source only** — no paid APIs. We learn by building, not by calling services.
- **Plug-and-play TTS** — swap TTS engines per character role without changing other services.
- **Code-based orchestration** — the file-server orchestrates the pipeline in Python, making the flow testable and reviewable.
- **English only** for now. Multi-language is a future concern.

---

## 2. Pipeline Overview

```
  ┌──────────────────┐         ┌──────────────────┐
  │  React Frontend  │────────▶│  NestJS Backend  │
  │  (Vite PWA)      │         │  (API gateway)   │
  └──────────────────┘         └────────┬─────────┘
                                        │ HTTP proxy
                               ┌────────▼─────────┐
                               │   File Server     │
                               │  (orchestrator)   │
                               │     :8080         │
                               └────────┬──────────┘
                                        │ async HTTP calls
        ┌───────────┬────────┴──────────┬───────────┐
        ▼           ▼                   ▼           ▼
   ┌─────────┐ ┌──────────┐    ┌─────────┐ ┌─────────┐
   │  Text   │ │TTS Router│    │ Audio   │ │   QA    │
   │Analyzer │ │  :8010   │    │Assembly │ │Verifier │
   │ :8001   │ └────┬─────┘    │ :8005   │ │ :8006   │
   └─────────┘      │          └─────────┘ └─────────┘
      LLM      ┌────┴────┐      ffmpeg     Whisper
                            ▼        ▼
                       ┌──────┐ ┌──────────┐  ┌─────────────┐
                       │XTTS  │ │ Qwen3-   │  │ <future>    │
                       │  v2  │ │   TTS    │  │  engine     │
                       │:8003 │ │  :8007   │  │  :xxxx      │
                       └──────┘ └──────────┘  └─────────────┘
                         GPU       GPU
```

Each box is a **Docker container** exposing a **FastAPI HTTP API**. The **file-server** orchestrates the pipeline — calling services via async HTTP, running TTS synthesis in parallel, and writing status updates for the frontend to poll. The **NestJS backend** acts as an API gateway, proxying requests from the React frontend to the file-server.

---

## 3. Pipeline Stages (Detailed)

### Stage 1: Text Analyzer (Hybrid Pipeline)

**Purpose:** Parse raw chapter text into structured segments with speaker attribution and emotion.

- **Input:** Plain text (a chapter or section)
- **Output:** JSON with character registry + ordered segments + pipeline report
- **Architecture:** 8-node pipeline — 6 deterministic (programmatic) nodes + 2 AI nodes (Ollama)

The text analyzer uses a **hybrid approach** that combines deterministic parsing for accuracy
and speed with targeted LLM calls only where language understanding is genuinely required.
This replaces the previous monolithic LLM approach that was slower and required post-processing
guards to fix fabrication, misattribution, and verbatim-text violations.

#### Pipeline Nodes

| # | Node | Type | Purpose |
|---|------|------|---------|
| 1 | **Segment Splitter** | programmatic | State machine splits text at quote boundaries into dialogue vs narration. Guarantees verbatim text by construction. |
| 2 | **Explicit Attribution** | programmatic | Regex patterns match "said X", "X whispered", etc. in adjacent narration. Resolves 60-80% of dialogue in typical fiction. |
| 3 | **Turn-Taking** | programmatic | Alternation heuristic for multi-character conversations where attributions drop. |
| 4 | **Character Registry** | programmatic | Builds character list from all discovered attributions. Tracks gender from pronoun usage. |
| 5 | **Pause/Timing** | programmatic | Assigns `pause_before_ms` from structural cues (paragraph breaks, scene breaks, dialogue turns). |
| 6 | **Validation** | programmatic | Verifies every word of the input appears in exactly one segment. Logs issues but does not fail the request. |
| 7 | **AI Attribution** | ai (Ollama) | Resolves remaining unknown speakers by sending surrounding context + character list to a small LLM. Returns only speaker names — no text generation. |
| 8 | **Emotion Classifier** | ai (Ollama) | Batched LLM call classifies emotion + intensity for all segments. Allowed values: neutral, happy, sad, angry, fearful, excited, tense, contemplative. |

Programmatic nodes complete in under 50ms total. AI nodes account for the bulk of processing
time (~5-20s per chapter depending on how many segments need AI resolution).

#### File structure

```
services/text-analyzer/app/
  main.py                        — FastAPI endpoint (POST /analyze, GET /health)
  pipeline.py                    — Orchestrator: runs nodes in sequence, collects per-node metrics
  models.py                      — Segment dataclass, NodeMetrics, PipelineResult
  nodes/
    segment_splitter.py          — Node 1: character-by-character state machine
    explicit_attribution.py      — Node 2: regex speech-verb pattern matching
    turn_taking.py               — Node 3: speaker alternation heuristic
    character_registry.py        — Node 4: character list builder
    pause_timing.py              — Node 5: structural pause assignment
    validation.py                — Node 6: text completeness verification
    ai_attribution.py            — Node 7: LLM-based ambiguous speaker resolution
    emotion_classifier.py        — Node 8: batched LLM emotion classification
  data/
    speech_verbs.txt             — ~100 speech verbs for Node 2 (said, whispered, shouted, etc.)
  prompts/
    ai_attribution_system.txt    — System prompt for Node 7
    ai_attribution_user.txt      — User prompt template for Node 7
    emotion_system.txt           — System prompt for Node 8
    emotion_user.txt             — User prompt template for Node 8
```

#### Output schema

```json
{
  "title": "Chapter 1: The Beginning",
  "characters": [
    {"name": "narrator", "description": "the narrative voice"},
    {"name": "Elena", "description": "female, 5 dialogue segment(s)"}
  ],
  "segments": [
    {
      "id": 1,
      "speaker": "narrator",
      "original_text": "The door creaked open. Elena stepped inside, her heart pounding.",
      "emotion": "tense",
      "intensity": 0.6,
      "pause_before_ms": 0
    },
    {
      "id": 2,
      "speaker": "Elena",
      "original_text": "Is anyone there?",
      "emotion": "fearful",
      "intensity": 0.7,
      "pause_before_ms": 300
    }
  ],
  "report": {
    "total_duration_ms": 17734,
    "programmatic_duration_ms": 19,
    "ai_duration_ms": 17715,
    "nodes": [
      {"node": "segment_splitter", "type": "programmatic", "duration_ms": 0, "segments_processed": 11, "segments_affected": 11},
      {"node": "explicit_attribution", "type": "programmatic", "duration_ms": 19, "segments_processed": 11, "segments_affected": 0},
      {"node": "ai_attribution", "type": "ai", "duration_ms": 4310, "segments_processed": 4, "segments_affected": 4},
      {"node": "emotion_classifier", "type": "ai", "duration_ms": 13405, "segments_processed": 11, "segments_affected": 8}
    ]
  }
}
```

The `report` field is a backward-compatible addition — downstream services ignore it.

### Stage 2: Voice Casting (Configuration)

**Not an AI stage** — `voice-cast.yaml` maps characters to TTS engines and voice profiles.
The `tts_service` field is the live routing key: the orchestrator reads it at synthesis time
and sets the `engine` field on each segment, which the TTS Router uses to pick the correct backend.

```yaml
voices:
  narrator:
    tts_service: xtts-v2        # engine value forwarded to tts-router
    tts_port: 8003
    reference_audio: /voices/narrator.wav
    speed: 0.95
    style_notes: calm, measured, storytelling tone
    qwen_speaker: Serena        # Qwen3-TTS predefined voice (if switching to qwen3-tts)
    qwen_instruct: calm and measured storytelling voice

  Elena:
    tts_service: xtts-v2
    tts_port: 8003
    reference_audio: /voices/elena.wav
    speed: 1.0
    style_notes: young, curious, energetic
    qwen_speaker: Vivian
    qwen_instruct: young energetic female voice

  default:
    tts_service: xtts-v2
    tts_port: 8003
    reference_audio: /voices/generic_neutral.wav
    speed: 1.0
    style_notes: neutral
    qwen_speaker: Ryan
    qwen_instruct: neutral clear voice
```

- Characters not in the config fall back to `default`
- **To switch a character to a different TTS engine:** change `tts_service` (and `tts_port`). That's it — no code changes anywhere.
- Each TTS engine reads its own fields (`reference_audio` for xtts-v2, `qwen_speaker`/`qwen_instruct` for qwen3-tts) and ignores the rest.

### Stage 3: TTS Synthesis

**Purpose:** Generate audio for each segment.

All synthesis requests go through the **TTS Router** (`:8010`), which forwards to the correct
backend based on the `engine` field in the request. The orchestrator always calls the router —
it never calls a TTS engine directly. TTS for multiple segments runs in parallel via
`asyncio.Semaphore` (configurable concurrency via `TTS_CONCURRENCY` env var, default 3).

**Shared API contract** (all TTS services implement this):

```
POST /synthesize
{
  "text": "Is anyone there?",
  "speaker": "Elena",
  "engine": "xtts-v2",            ← routing key; which backend to use
  "reference_audio_path": "/voices/elena.wav",  ← xtts-v2 only; ignored by others
  "emotion": "fearful",
  "intensity": 0.7,
  "speed": 1.0
}
→ returns: JSON { segment_id, speaker, file_path, filename }
```

**TTS Router** (`:8010`) — routes by `engine` field
- Backend map configured via `TTS_BACKENDS` env var (JSON string in docker-compose)
- Falls back to `DEFAULT_ENGINE` if the requested engine is unknown
- Adding a new engine: new Docker service + one entry in `TTS_BACKENDS`. Zero code changes.

**Available TTS engines:**

| Engine | Port | Mechanism | Voice control |
|---|---|---|---|
| `xtts-v2` | 8003 | Voice cloning from reference WAV | `reference_audio_path` per character |
| `qwen3-tts` | 8007 | 9 predefined voices + instruction control | `qwen_speaker` + `qwen_instruct` from voice-cast.yaml; `emotion` field mapped to natural-language instruct |

**Qwen3-TTS predefined voices:** Vivian, Serena, Uncle_Fu, Dylan, Eric, Ryan, Aiden, Ono_Anna, Sohee

**Emotion → instruct mapping** (qwen3-tts):
- `neutral` → no instruction (model default)
- `happy` → "speak with warmth and cheerfulness"
- `sad` → "speak with a tone of sadness and melancholy"
- `angry` → "speak very angrily with sharp emphasis"
- `fearful` → "speak with a trembling, nervous, fearful tone"
- `excited` → "speak with high energy and excitement"
- `tense` → "speak with urgency and tension in your voice"
- `contemplative` → "speak slowly and thoughtfully, as if pondering deeply"

**Future TTS engines to evaluate:**
- **F5-TTS** — newer, very natural, good zero-shot cloning
- **Bark** — best emotion control (laughing, sighing, hesitation)
- **Kokoro** — lightweight and fast for bulk generation
- **Piper** — very fast, lower quality, good for drafts/previews

### Stage 4: Audio Assembly

**Purpose:** Combine individual segment audio clips into a complete chapter.

- **Input:** Ordered audio clips + segment metadata (pause_before_ms, etc.)
- **Output:** Single audio file (WAV or MP3), optionally M4B with chapter markers
- **No AI involved** — deterministic audio processing
- **Operations:**
  - Concatenate clips in segment order
  - Insert pauses between segments (using pause_before_ms from metadata)
  - Normalize volume across all speakers (consistent loudness)
  - Crossfade between segments for smooth transitions
  - Apply light audio cleanup (remove leading/trailing silence per clip)
- **Tools:** ffmpeg + pydub

### Pipeline Orchestrator (file-server)

**Purpose:** Drive the entire pipeline end-to-end.

Lives in `services/file-server/app/orchestrator.py`. Two async entry points:

- **`run_analyze(client, job_id, title, text)`** — calls text-analyzer, writes status updates
- **`run_synthesize(client, job_id, segments, voice_mapping, engine_mapping)`** — runs parallel TTS → audio-assembly, writes status updates at each stage

The file-server's `/api/analyze` and `/api/synthesize` endpoints launch these as background tasks
(`asyncio.create_task`) and return the `job_id` immediately. The frontend polls `/api/status/{job_id}`
for progress.

**Parallel TTS:** Segments are synthesized concurrently via `asyncio.gather` with an
`asyncio.Semaphore(TTS_CONCURRENCY)` to bound GPU memory usage. Progress (completed/total) is
written to the status file after each segment completes.

**Post-assembly cleanup:** After successful assembly, intermediate per-segment audio files are
deleted automatically.

```
services/file-server/app/
  main.py               — FastAPI endpoints, persistent httpx.AsyncClient via lifespan
  orchestrator.py       — Pipeline orchestration logic (run_analyze, run_synthesize)
```

---

### Hosted Frontend & Backend

The `hosted/` directory contains the user-facing web application:

**React Frontend** (`hosted/frontend/`) — Vite-powered PWA with:
- `src/hooks/` — `usePipeline` (state + polling), `usePolling` (generic), `useAudioPreview`, `useVoiceRecorder`
- `src/components/` — AnalyzeForm, VoiceCast, PostProduction (with SegmentCard), VoiceManager (with VoiceRecorder, VoiceList), StatusProgress, PipelineMap, ServiceHealth
- `src/constants/` — shared engine definitions and emotions
- `src/utils/` — formatError, formatDuration, encodeWav
- `src/api.ts` — shared `request<T>()` helper for all API calls

**NestJS Backend** (`hosted/backend/`) — API gateway that proxies to file-server:
- `src/proxy/` — split into domain controllers: HealthController, VoicesController, AudioController, StatusController, PipelineController
- `src/filters/all-exceptions.filter.ts` — global exception filter with consistent error shape
- `src/interceptors/logging.interceptor.ts` — request/response logging with duration
- `src/pipes/path-traversal.pipe.ts` — validates filename params against `../` traversal
- Security: helmet, CORS restriction, `@nestjs/throttler` rate limiting, file upload size limits
- TypeScript strict mode enabled

---

### Stage 6: QA Verifier

**Purpose:** Automatically check if the generated audio matches the source text.

- **Input:** Final audio + original segments JSON
- **Output:** QA report with per-segment scores + flagged issues
- **Model:** OpenAI Whisper (large-v3) for speech-to-text
- **Process:**
  1. Run Whisper on the full audio (or per-segment clips)
  2. Compare transcription to `spoken_text` from segments
  3. Calculate word error rate (WER) per segment
  4. Flag segments above a WER threshold for re-generation
  5. Detect audio issues: clipping, excessive silence, volume anomalies
- **Output:** QA report JSON + list of segment IDs that need re-synthesis

---

## 4. Infrastructure

### Runtime Environment

- **Hardware:** NVIDIA DGX Spark (Grace Blackwell, 128GB unified memory)
- **Orchestration:** Docker Compose (all services)
- **GPU allocation:** TTS services and Whisper get GPU access; LLM runs through Ollama (manages its own GPU memory)

### Docker Compose Services

| Service          | Port  | GPU | Description                         |
|------------------|-------|-----|-------------------------------------|
| ollama           | 11435 | Yes | Shared LLM backend (host port 11435 → internal 11434) |
| text-analyzer    | 8001  | No  | Hybrid pipeline: 6 programmatic nodes + 2 AI nodes (Ollama) |
| xtts-v2          | 8003  | Yes | FastAPI + XTTS v2 (voice cloning)   |
| qwen3-tts        | 8007  | Yes | FastAPI + Qwen3-TTS-12Hz-1.7B (predefined voices + instruct) |
| tts-router       | 8010  | No  | HTTP proxy — routes /synthesize to correct TTS backend |
| audio-assembly   | 8005  | No  | FastAPI + ffmpeg/pydub              |
| qa-verifier      | 8006  | Yes | FastAPI + Whisper (Phase 2)         |
| file-server      | 8080  | No  | Pipeline orchestrator, file serving, status API |

All services have Docker healthchecks. Services that depend on model loading (text-analyzer)
use `depends_on: condition: service_healthy` on ollama. GPU services (xtts-v2,
qwen3-tts) have a 120s `start_period` to allow model loading.

### Shared Volumes

- `./voices/` — reference audio clips for voice cloning
- `./input/` — source text files (chapters)
- `./output/` — generated audiobooks
- `./data/intermediate/` — segment JSONs and per-segment audio clips

### Environment Variables

| Variable | Default | Service(s) | Description |
|----------|---------|------------|-------------|
| `LOG_LEVEL` | `INFO` | All Python services | Python logging level |
| `OLLAMA_TIMEOUT_S` | `300` | text-analyzer | Timeout for Ollama LLM calls (seconds) |
| `TTS_CONCURRENCY` | `3` | file-server | Max parallel TTS synthesis requests |
| `TTS_BACKENDS` | (JSON) | tts-router | Engine→URL map for TTS routing |
| `DEFAULT_ENGINE` | `xtts-v2` | tts-router | Fallback TTS engine |
| `DGX_URL` | — | NestJS backend | File-server URL for proxying |
| `CORS_ORIGIN` | `*` | NestJS backend | Allowed CORS origin |
| `DGX_TIMEOUT_MS` | `300000` | NestJS backend | Upstream request timeout |

---

## 5. MVP Scope (Phase 1) ✅

**Goal:** End-to-end pipeline that takes a chapter and produces an audiobook file with multiple character voices.

**Includes:**
- File-server orchestrator driving the pipeline
- Text Analyzer service (hybrid pipeline: deterministic parsing + targeted Ollama calls)
- One TTS service: XTTS v2
- Audio Assembly service
- Voice cast config (YAML)
- Docker Compose for the full stack

**Excludes (for now):**
- QA Verifier (Phase 2)
- Voice cloning from your own voice (Phase 3)
- Full book processing / batch mode (Phase 4)
- M4B audiobook format with chapters (Phase 4)

---

## 6. Future Phases

### Phase 2 — QA + Reliability
- Add Whisper-based QA Verifier
- Auto-retry failed segments

### Phase 3 — Multi-TTS + Voice Cloning ✅
- ✅ TTS Router added — single entry point, routes by `engine` field in request
- ✅ Qwen3-TTS-12Hz-1.7B added — 9 predefined voices, emotion via natural-language instruct
- ✅ Per-character engine assignment via `tts_service` in voice-cast.yaml
- ✅ Adding future engines requires zero code changes (new service + TTS_BACKENDS env entry)
- Clone your voice for the narrator
- Evaluate F5-TTS, Bark, Kokoro, Piper

### Phase 4 — Polish + Scale
- ✅ Web UI for voice casting, post-production, pipeline visualization (React + NestJS)
- ✅ Parallel TTS generation via orchestrator semaphore
- Full book processing (chapter splitting, batch queuing)
- M4B output with chapter markers
- Background music / ambient sound layer

### Phase 5 — Advanced
- Fine-tune TTS models on specific character voices
- Emotion training with custom datasets
- MCP server wrappers for each agent (enabling use from Claude Code / other AI tools)
- Multi-language support

---

## 7. Key Decisions Log

| Date       | Decision                                | Rationale                                                    |
|------------|----------------------------------------|--------------------------------------------------------------|
| 2026-02-17 | Open-source only, no paid APIs          | Learning-focused project, full control over pipeline         |
| 2026-02-17 | ~~n8n as orchestrator from day 1~~          | Replaced — see 2026-03-01 entry |
| 2026-02-17 | Each stage = Docker container + FastAPI | Independent scaling, easy swapping, testable in isolation    |
| 2026-02-17 | XTTS v2 as MVP TTS engine              | Battle-tested, good cloning, large community                 |
| 2026-02-17 | English only for MVP                    | Reduces complexity, most TTS models handle English best      |
| 2026-02-17 | One TTS for MVP, multi-TTS later        | Get end-to-end working first, then expand                    |
| 2026-02-17 | Shared Ollama instance for LLM stages   | Text-analyzer uses Ollama for AI attribution and emotion classification |
| 2026-02-24 | TTS Router as single entry point         | Orchestrator calls tts-router:8010; router dispatches by `engine` field; adding future engines = zero code changes |
| 2026-02-24 | `engine` field in SynthesizeRequest     | Explicit routing key preferred over implicit speaker→YAML lookup in router; caller owns the routing decision |
| 2026-02-24 | Qwen3-TTS as second TTS engine          | Predefined voices + instruction-based emotion control; good complement to XTTS v2's voice-cloning approach |
| 2026-02-26 | Hybrid text-analyzer pipeline            | Replaced monolithic LLM call with 8-node pipeline (6 programmatic + 2 AI). Deterministic parsing guarantees verbatim text and eliminates post-processing guards. AI used only for ambiguous speaker attribution and emotion classification. ~18s per chapter vs ~1-3min before. |
| 2026-03-01 | Replace n8n with Python orchestrator     | n8n was untestable, unreviewable, and added an extra proxy layer. Moved pipeline logic to `file-server/app/orchestrator.py` — testable Python, parallel TTS via asyncio.Semaphore, same status-polling contract. |
| 2026-03-01 | Persistent httpx.AsyncClient             | Per-request client creation caused connection overhead. All services now use a persistent client created at startup via FastAPI lifespan. |
| 2026-03-01 | Sync handlers for blocking TTS/audio     | FastAPI auto-offloads sync handlers to a threadpool, preventing event loop blocking during GPU inference and audio processing. |
| 2026-03-01 | NestJS controller split + security       | Split monolithic ProxyController into domain controllers. Added helmet, CORS restriction, rate limiting, path traversal validation, global exception filter. Enabled TypeScript strict mode. |
| 2026-03-01 | React hook extraction pattern             | Split 268-line App.tsx into thin shell + usePipeline/usePolling hooks. Extracted shared hooks (useAudioPreview, useVoiceRecorder), constants, and sub-components to eliminate duplication. |

---

## Changelog

- **2026-02-17:** Initial architecture document created. Defined 6-stage pipeline, MVP scope, and phased roadmap.
- **2026-02-24:** Added TTS Router (`tts-router:8010`) and Qwen3-TTS (`qwen3-tts:8007`). Introduced `engine` field in shared `SynthesizeRequest` contract. voice-cast.yaml `tts_service` field now drives engine selection per character via n8n code node. Updated pipeline diagram, services table, Phase 3 status.
- **2026-02-26:** Replaced text-analyzer's monolithic LLM approach with an 8-node hybrid pipeline. 6 programmatic nodes handle segment splitting (state machine), speaker attribution (regex + turn-taking heuristic), character registry, pause timing, and validation. 2 AI nodes handle ambiguous speaker resolution and emotion classification via targeted Ollama calls. Added per-node `report` field to the API response for timing/attribution breakdown. Same API contract — downstream services unchanged.
- **2026-03-01:** Major refactoring across all layers:
  - **Removed n8n.** Pipeline orchestration moved to `services/file-server/app/orchestrator.py` — two async functions (`run_analyze`, `run_synthesize`) replace both n8n workflows. Parallel TTS via `asyncio.Semaphore`. Post-assembly intermediate file cleanup. Status-polling contract unchanged.
  - **Fixed critical Python bugs.** Converted blocking async TTS/audio handlers to sync (FastAPI threadpool offloading). Replaced per-request `httpx.AsyncClient` with persistent clients via lifespan. Fixed XTTS-v2 hardcoded voice cast (now loads from voice-cast.yaml). Fixed tts-router silently dropping unknown fields (now forwards raw request body). Added Ollama timeout (300s default). Added audio normalization guard for silent audio.
  - **Python best practices.** Migrated all services from deprecated `@app.on_event("startup")` to `@asynccontextmanager lifespan`. Standardized health checks (`{"status": "ok", "service": "<name>"}`). Added Docker healthchecks to all services with `depends_on: condition: service_healthy`. Unified logging with configurable `LOG_LEVEL`. Added `.dockerignore` to all services. Pinned all dependencies.
  - **React frontend refactoring.** Extracted `usePipeline`, `usePolling`, `useAudioPreview`, `useVoiceRecorder` hooks. Split VoiceManager into sub-components (VoiceRecorder, VoiceList). Extracted SegmentCard from PostProduction. Deduplicated constants (engines, emotions), utilities (formatError, formatDuration, encodeWav), and types. Fixed stale closure bugs. Replaced `alert()` with inline validation. Split monolithic CSS into component-scoped files.
  - **NestJS backend hardening.** Restricted CORS to configured origin. Added helmet middleware, rate limiting (`@nestjs/throttler`), path traversal validation pipe. Created global `AllExceptionsFilter` and `LoggingInterceptor`. Split monolithic `ProxyController` into `HealthController`, `VoicesController`, `AudioController`, `StatusController`, `PipelineController`. Enabled TypeScript strict mode. Optimized Dockerfile (`npm ci --omit=dev`).
  - **Testing infrastructure.** Added pytest suites for text-analyzer (16 tests: segment splitter, explicit attribution, validation) and file-server orchestrator (3 tests with httpx mock transport). All 19 tests passing. Added `.env.example`.
