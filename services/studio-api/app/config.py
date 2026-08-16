"""Paths and shared request guards for the studio API."""

import os
from pathlib import Path

from fastapi import HTTPException

from core.jobs.workspace import Workspace

VOICES_DIR = os.getenv("VOICES_DIR", "/voices")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
STATIC_DIR = os.getenv("STATIC_DIR", "/static")

# Where each run leaves its stage artifacts. Under OUTPUT_DIR by default so it
# lands on a volume that already exists.
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", os.path.join(OUTPUT_DIR, "workspace"))

# Clips are written by the synthesiser into a volume shared with assembly, so a
# manifest may point outside the job directory. Serving one is only allowed
# from somewhere this service is meant to read.
CLIP_ROOTS = tuple(Path(p).resolve() for p in (
    WORKSPACE_DIR, OUTPUT_DIR, os.getenv("INTERMEDIATE_DIR", "/data/intermediate"),
))


def workspace() -> Workspace:
    return Workspace(Path(WORKSPACE_DIR))

VALID_ENGINES = {"xtts", "qwen3"}


def safe_filename(name: str) -> str:
    """Validate a user-supplied filename, raising 400 on path traversal."""
    if ".." in name or "/" in name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    return name


def engine_dir(engine: str) -> str:
    """Return the subdirectory for a given engine, raising 400 on invalid values."""
    if engine not in VALID_ENGINES:
        raise HTTPException(status_code=400, detail=f"Invalid engine: {engine}")
    return os.path.join(VOICES_DIR, engine)
