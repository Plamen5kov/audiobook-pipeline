"""Shared Ollama HTTP helper for AI-powered pipeline nodes.

Provides a single async function that handles the HTTP call, error
handling, and JSON parsing common to ai_attribution and emotion_classifier.
"""

from __future__ import annotations

import json
import logging

import httpx

log = logging.getLogger(__name__)


async def call_ollama(
    ollama_url: str,
    model_name: str,
    system_prompt: str,
    user_prompt: str,
) -> dict:
    """Send a prompt to Ollama's ``/api/generate`` endpoint and return
    the parsed JSON response body.

    Raises on HTTP errors or malformed JSON so callers can handle
    failures at the node level.
    """
    async with httpx.AsyncClient(timeout=None) as client:
        resp = await client.post(
            f"{ollama_url}/api/generate",
            json={
                "model": model_name,
                "prompt": user_prompt,
                "system": system_prompt,
                "stream": False,
                "format": "json",
                "options": {"num_predict": -1},
            },
        )
        resp.raise_for_status()

    raw = resp.json().get("response", "")
    return json.loads(raw)
