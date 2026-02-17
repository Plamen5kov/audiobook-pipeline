# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Audiobook generation pipeline — takes a book chapter as input and produces an audiobook with distinct voices per character and a narrator. All open-source models, running locally on NVIDIA DGX Spark.

## Architecture

See `ARCHITECTURE.md` for the full design document (source of truth).

The pipeline is orchestrated by **n8n** (visual workflow builder) calling **FastAPI microservices** in Docker containers:

- **text-analyzer** (:8001) — LLM parses text into structured segments with speaker/emotion metadata
- **script-adapter** (:8002) — LLM rewrites text for spoken delivery
- **xtts-v2** (:8003) — TTS synthesis using Coqui XTTS v2
- **audio-assembly** (:8005) — Combines clips into final audiobook (ffmpeg/pydub)
- **qa-verifier** (:8006) — Whisper-based transcription comparison (Phase 2)
- **ollama** (:11434) — Shared LLM backend for text-analyzer and script-adapter
- **n8n** (:5678) — Pipeline orchestrator and visual UI

## Commands

```bash
# Start all services
docker compose up -d

# Start specific service
docker compose up -d text-analyzer

# View logs
docker compose logs -f <service-name>

# Rebuild after code changes
docker compose up -d --build <service-name>

# Run a single service locally (for development)
cd services/<service-name>
pip install -r requirements.txt
uvicorn app.main:app --reload --port <port>
```

## Conventions

- Each service lives in `services/<name>/` with its own `Dockerfile`, `requirements.txt`, and `app/main.py`
- All TTS services must implement the same API contract: `POST /synthesize` (see ARCHITECTURE.md Stage 4)
- Voice cast configuration is in `voice-cast.yaml` at the project root
- Intermediate data (segment JSONs, per-segment audio) goes in `data/intermediate/`
- Reference voice clips go in `voices/`
- n8n workflows are persisted in `n8n-data/`
