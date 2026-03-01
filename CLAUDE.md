# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Audiobook generation pipeline — takes a book chapter as input and produces an audiobook with distinct voices per character and a narrator. All open-source models, running locally on NVIDIA DGX Spark.

## Architecture

See `ARCHITECTURE.md` for the full design document (source of truth).

The pipeline is orchestrated by the **file-server** (`services/file-server/app/orchestrator.py`) calling **FastAPI microservices** in Docker containers:

- **file-server** (:8080) — Pipeline orchestrator, file serving, status API
- **text-analyzer** (:8001) — Hybrid pipeline parses text into structured segments with speaker/emotion metadata
- **tts-router** (:8010) — Routes TTS requests to the correct engine by `engine` field
- **xtts-v2** (:8003) — TTS synthesis using Coqui XTTS v2
- **qwen3-tts** (:8007) — TTS synthesis using Qwen3-TTS (predefined voices + instruct)
- **audio-assembly** (:8005) — Combines clips into final audiobook (ffmpeg/pydub)
- **ollama** (:11434) — Shared LLM backend for text-analyzer
- **NestJS backend** (hosted/backend) — API gateway proxying React frontend to file-server

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
- All services use FastAPI `lifespan` context manager (not deprecated `@app.on_event`)
- All services use persistent `httpx.AsyncClient` created in lifespan (not per-request)
- All TTS services must implement the same API contract: `POST /synthesize` (see ARCHITECTURE.md Stage 4)
- Voice cast configuration is in `voice-cast.yaml` at the project root
- Intermediate data (segment JSONs, per-segment audio) goes in `data/intermediate/`
- Reference voice clips go in `voices/`
- Tests live in `services/<name>/tests/` with `pytest.ini` in the service root
- Hosted frontend (React/Vite) is in `hosted/frontend/`, backend (NestJS) in `hosted/backend/`
