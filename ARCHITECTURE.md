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
        ┌───────────┬───────────┬───┴────┬──────────┬───────────┐
        ▼           ▼           ▼        ▼          ▼           ▼
   ┌─────────┐ ┌─────────┐ ┌──────┐ ┌──────┐ ┌─────────┐ ┌─────────┐
   │  Text   │ │ Script  │ │ TTS  │ │ TTS  │ │ Audio   │ │   QA    │
   │Analyzer │ │Adapter  │ │  #1  │ │  #2  │ │Assembly │ │Verifier │
   │ :8001   │ │ :8002   │ │:8003 │ │:8004 │ │ :8005   │ │ :8006   │
   └─────────┘ └─────────┘ └──────┘ └──────┘ └─────────┘ └─────────┘
      LLM         LLM        GPU      GPU      ffmpeg     Whisper
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

**Not an AI stage** — this is a config file that maps characters to TTS engines and voice profiles.

```yaml
voices:
  narrator:
    tts_service: xtts_v2        # which TTS container to call
    reference_audio: ./voices/my_voice.wav
    speed: 0.95
    style_notes: calm, measured, storytelling tone

  elena:
    tts_service: xtts_v2        # same or different engine
    reference_audio: ./voices/elena_ref.wav
    speed: 1.0
    style_notes: young, energetic

  default:
    tts_service: xtts_v2
    reference_audio: ./voices/generic_neutral.wav
    speed: 1.0
    style_notes: neutral
```

- Characters not in the config fall back to `default`
- n8n reads this config and routes segments to the correct TTS service
- Changing a character's voice = edit YAML + re-run. No code changes.

### Stage 4: TTS Synthesis

**Purpose:** Generate audio for each segment.

**Shared API contract** (all TTS services implement this):

```
POST /synthesize
{
  "text": "Is anyone there?",
  "speaker_id": "elena",
  "reference_audio_path": "/voices/elena_ref.wav",
  "emotion": "fearful",
  "intensity": 0.7,
  "speed": 1.0
}
→ returns: audio/wav file
```

**MVP TTS engine: XTTS v2 (Coqui)**
- Voice cloning from short reference clips (~6-10 seconds)
- Good English quality
- Supports emotion through reference audio conditioning
- Battle-tested, large community

**Future TTS engines to add:**
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
| ollama           | 11434 | Yes | Shared LLM backend                  |
| text-analyzer    | 8001  | No  | FastAPI, calls Ollama               |
| script-adapter   | 8002  | No  | FastAPI, calls Ollama               |
| xtts-v2          | 8003  | Yes | FastAPI + XTTS v2 model             |
| audio-assembly   | 8005  | No  | FastAPI + ffmpeg/pydub              |
| qa-verifier      | 8006  | Yes | FastAPI + Whisper                   |

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

### Phase 3 — Multi-TTS + Voice Cloning
- Add F5-TTS and/or Bark as alternative TTS engines
- n8n routing: different TTS per character role
- Clone your voice for the narrator
- Experiment with emotion-conditioned generation

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

---

## Changelog

- **2026-02-17:** Initial architecture document created. Defined 6-stage pipeline, MVP scope, and phased roadmap.
