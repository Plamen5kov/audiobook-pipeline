# Audiobook Pipeline — Architecture & Plan

> **Source of truth** for the project's design, decisions, and roadmap.
> Update this document as the architecture evolves. Keep a changelog at the bottom.

---

## 1. Vision

Turn a book chapter (plain text) into a full audiobook with **distinct voices per character**, a dedicated **narrator voice**, and appropriate **emotional delivery** — all using open-source models running locally on an NVIDIA DGX Spark.

### Core Principles

- **Open-source only** — no paid APIs. We learn by building, not by calling services.
- **Plug-and-play TTS** — swap TTS engines per character role without changing other services.
- **Visual orchestration** — n8n as the pipeline orchestrator from day 1. Every stage is a service, every connection is visible.
- **English only** for now. Multi-language is a future concern.

---

## 2. Pipeline Overview

```
                         ┌─────────────────────┐
                         │       n8n            │
                         │  (visual pipeline    │
                         │   orchestrator)      │
                         │     :5678            │
                         └──────────┬───────────┘
                                    │ HTTP calls
        ┌───────────┬───────────┬───┴──────────┬───────────┐
        ▼           ▼           ▼              ▼           ▼
   ┌─────────┐ ┌─────────┐ ┌──────────┐ ┌─────────┐ ┌─────────┐
   │  Text   │ │ Script  │ │TTS Router│ │ Audio   │ │   QA    │
   │Analyzer │ │Adapter  │ │  :8010   │ │Assembly │ │Verifier │
   │ :8001   │ │ :8002   │ └────┬─────┘ │ :8005   │ │ :8006   │
   └─────────┘ └─────────┘      │       └─────────┘ └─────────┘
      LLM         LLM      ┌────┴────┐    ffmpeg     Whisper
                            ▼        ▼
                       ┌──────┐ ┌──────────┐  ┌─────────────┐
                       │XTTS  │ │ Qwen3-   │  │ <future>    │
                       │  v2  │ │   TTS    │  │  engine     │
                       │:8003 │ │  :8007   │  │  :xxxx      │
                       └──────┘ └──────────┘  └─────────────┘
                         GPU       GPU
```

Each box is a **Docker container** exposing a **FastAPI HTTP API**. n8n orchestrates the flow, routes segments to the correct TTS engine, and provides the visual UI for experimenting with different configurations.

---

## 3. Pipeline Stages (Detailed)

### Stage 1: Text Analyzer (LLM)

**Purpose:** Parse raw chapter text into structured segments.

- **Input:** Plain text (a chapter or section)
- **Output:** JSON with character registry + ordered segments
- **Model:** Llama 3.1 70B or Qwen 2.5 72B via shared Ollama instance
- **Responsibilities:**
  - Identify all characters present in the text
  - Attribute each line of dialogue to the correct speaker
  - Mark narration segments as `narrator`
  - Detect emotional tone per segment (neutral, happy, sad, angry, fearful, excited, etc.)
  - Estimate intensity (0.0–1.0)
  - Suggest pacing hints (pause duration before dramatic moments)

**Output schema:**

```json
{
  "title": "Chapter 1: The Beginning",
  "characters": [
    {"name": "narrator", "description": "Third person omniscient"},
    {"name": "Elena", "description": "Young woman, determined, mid-20s"}
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
      "original_text": "\"Is anyone there?\" she whispered.",
      "emotion": "fearful",
      "intensity": 0.7,
      "pause_before_ms": 300
    }
  ]
}
```

### Stage 2: Script Adapter (LLM)

**Purpose:** Rewrite text for optimal spoken delivery.

- **Input:** Segments JSON from Stage 1
- **Output:** Same JSON with added `spoken_text` field per segment
- **Model:** Same Ollama instance as Stage 1
- **Transformations:**
  - Strip dialogue attribution ("she whispered", "he said angrily") — emotion comes from TTS parameters, not spoken words
  - Expand abbreviations ("Dr." → "Doctor", "St." → "Street" or "Saint" based on context)
  - Convert numbers to words ("42" → "forty-two")
  - Adjust punctuation for natural pauses (add commas, ellipses where speech would pause)
  - Handle internal monologue differently from spoken dialogue

**After this stage, segment 2 from above becomes:**

```json
{
  "id": 2,
  "speaker": "Elena",
  "original_text": "\"Is anyone there?\" she whispered.",
  "spoken_text": "Is anyone there?",
  "emotion": "fearful",
  "intensity": 0.7,
  "pause_before_ms": 300
}
```

### Stage 3: Voice Casting (Configuration)

**Not an AI stage** — `voice-cast.yaml` maps characters to TTS engines and voice profiles.
The `tts_service` field is the live routing key: the n8n `apply voice mapping` node reads it
at synthesis time and sets the `engine` field on each segment, which the TTS Router uses to
pick the correct backend.

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

### Stage 4: TTS Synthesis

**Purpose:** Generate audio for each segment.

All synthesis requests go through the **TTS Router** (`:8010`), which forwards to the correct
backend based on the `engine` field in the request. n8n always calls the router — it never
calls a TTS engine directly.

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

### Stage 5: Audio Assembly

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
| n8n              | 5678  | No  | Pipeline orchestrator + UI          |
| ollama           | 11435 | Yes | Shared LLM backend (host port 11435 → internal 11434) |
| text-analyzer    | 8001  | No  | FastAPI, calls Ollama               |
| script-adapter   | 8002  | No  | FastAPI, calls Ollama               |
| xtts-v2          | 8003  | Yes | FastAPI + XTTS v2 (voice cloning)   |
| qwen3-tts        | 8007  | Yes | FastAPI + Qwen3-TTS-12Hz-1.7B (predefined voices + instruct) |
| tts-router       | 8010  | No  | HTTP proxy — routes /synthesize to correct TTS backend |
| audio-assembly   | 8005  | No  | FastAPI + ffmpeg/pydub              |
| qa-verifier      | 8006  | Yes | FastAPI + Whisper (Phase 2)         |
| file-server      | 8080  | No  | Serves voices, outputs; proxies n8n webhooks |

### Shared Volumes

- `./voices/` — reference audio clips for voice cloning
- `./input/` — source text files (chapters)
- `./output/` — generated audiobooks
- `./data/intermediate/` — segment JSONs and per-segment audio clips
- `./n8n-data/` — n8n workflow persistence

---

## 5. MVP Scope (Phase 1)

**Goal:** End-to-end pipeline that takes a chapter and produces an audiobook file with multiple character voices.

**Includes:**
- n8n running and orchestrating the full flow
- Text Analyzer service (Ollama + Llama 3.1)
- Script Adapter service (same Ollama)
- One TTS service: XTTS v2
- Audio Assembly service
- Voice cast config (YAML)
- Docker Compose for the full stack

**Excludes (for now):**
- QA Verifier (Phase 2)
- Multiple TTS engines (Phase 3)
- Voice cloning from your own voice (Phase 3)
- Web UI for voice casting (Phase 4)
- Full book processing / batch mode (Phase 4)
- M4B audiobook format with chapters (Phase 4)

---

## 6. Future Phases

### Phase 2 — QA + Reliability
- Add Whisper-based QA Verifier
- Auto-retry failed segments
- Better error handling across services

### Phase 3 — Multi-TTS + Voice Cloning ✅ (in progress)
- ✅ TTS Router added — single entry point, routes by `engine` field in request
- ✅ Qwen3-TTS-12Hz-1.7B added — 9 predefined voices, emotion via natural-language instruct
- ✅ Per-character engine assignment via `tts_service` in voice-cast.yaml
- ✅ Adding future engines requires zero code changes (new service + TTS_BACKENDS env entry)
- Clone your voice for the narrator
- Evaluate F5-TTS, Bark, Kokoro, Piper

### Phase 4 — Polish + Scale
- Web UI for voice casting (character → voice assignment with audio previews)
- Full book processing (chapter splitting, batch queuing)
- M4B output with chapter markers
- Background music / ambient sound layer
- Performance optimization (parallel TTS generation)

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
| 2026-02-17 | n8n as orchestrator from day 1          | Visual plug-and-play is a core requirement, not an afterthought |
| 2026-02-17 | Each stage = Docker container + FastAPI | Enables n8n integration, independent scaling, easy swapping  |
| 2026-02-17 | XTTS v2 as MVP TTS engine              | Battle-tested, good cloning, large community                 |
| 2026-02-17 | English only for MVP                    | Reduces complexity, most TTS models handle English best      |
| 2026-02-17 | One TTS for MVP, multi-TTS later        | Get end-to-end working first, then expand                    |
| 2026-02-17 | Shared Ollama instance for LLM stages   | Both text-analyzer and script-adapter use the same model type|
| 2026-02-24 | TTS Router as single n8n entry point    | n8n calls tts-router:8010; router dispatches by `engine` field; adding future engines = zero code changes |
| 2026-02-24 | `engine` field in SynthesizeRequest     | Explicit routing key preferred over implicit speaker→YAML lookup in router; caller (n8n) owns the routing decision |
| 2026-02-24 | Qwen3-TTS as second TTS engine          | Predefined voices + instruction-based emotion control; good complement to XTTS v2's voice-cloning approach |

---

## Changelog

- **2026-02-17:** Initial architecture document created. Defined 6-stage pipeline, MVP scope, and phased roadmap.
- **2026-02-24:** Added TTS Router (`tts-router:8010`) and Qwen3-TTS (`qwen3-tts:8007`). Introduced `engine` field in shared `SynthesizeRequest` contract. voice-cast.yaml `tts_service` field now drives engine selection per character via n8n code node. Updated pipeline diagram, services table, Phase 3 status.
