"""Paths and shared request guards for the studio API."""

import os

from fastapi import HTTPException

VOICES_DIR = os.getenv("VOICES_DIR", "/voices")
OUTPUT_DIR = os.getenv("OUTPUT_DIR", "/output")
STATIC_DIR = os.getenv("STATIC_DIR", "/static")

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
