"""Tests for the pipeline orchestrator.

Uses httpx mocking to verify the orchestrator calls the right services
and writes correct status updates.
"""

import json
import os
import tempfile

import httpx
import pytest
import pytest_asyncio

# Patch OUTPUT_DIR before importing orchestrator.
_tmpdir = tempfile.mkdtemp()
os.environ["OUTPUT_DIR"] = _tmpdir

from app.orchestrator import run_analyze, run_synthesize


def _read_status(job_id: str) -> dict:
    path = os.path.join(_tmpdir, f"status_{job_id}.json")
    with open(path) as f:
        return json.load(f)


class MockTransport(httpx.AsyncBaseTransport):
    """Simple mock transport that returns pre-configured responses by URL."""

    def __init__(self, responses: dict[str, dict]):
        self._responses = responses

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        for pattern, resp_data in self._responses.items():
            if pattern in url:
                return httpx.Response(
                    status_code=resp_data.get("status", 200),
                    json=resp_data.get("json", {}),
                )
        return httpx.Response(status_code=404, json={"error": "not found"})


@pytest.mark.asyncio
async def test_run_analyze_success():
    transport = MockTransport({
        "/analyze": {
            "json": {
                "title": "Test",
                "segments": [{"id": 1, "speaker": "narrator", "original_text": "Hello"}],
                "characters": [],
                "report": {},
            }
        }
    })
    client = httpx.AsyncClient(transport=transport)

    await run_analyze(client, "test-job-1", "Test Title", "Hello world")

    status = _read_status("test-job-1")
    assert status["phase"] == "analyzing"
    assert status["status"] == "done"
    assert len(status["segments"]) == 1

    await client.aclose()


@pytest.mark.asyncio
async def test_run_analyze_error():
    transport = MockTransport({
        "/analyze": {"status": 500, "json": {"detail": "LLM failed"}},
    })
    client = httpx.AsyncClient(transport=transport)

    await run_analyze(client, "test-job-err", "Title", "Text")

    status = _read_status("test-job-err")
    assert status["status"] == "error"
    assert "error" in status

    await client.aclose()


@pytest.mark.asyncio
async def test_run_synthesize_success():
    transport = MockTransport({
        "/synthesize": {
            "json": {
                "segment_id": 1,
                "speaker": "narrator",
                "file_path": "/data/intermediate/seg0001.wav",
                "filename": "seg0001.wav",
            }
        },
        "/assemble": {
            "json": {
                "filename": "chapter_test.wav",
                "duration_ms": 5000,
                "clips_count": 1,
            }
        },
    })
    client = httpx.AsyncClient(transport=transport)

    segments = [{"id": 1, "speaker": "narrator", "original_text": "Hello", "emotion": "neutral", "intensity": 0.5, "pause_before_ms": 0}]
    voice_mapping = {"narrator": "narrator.wav"}
    engine_mapping = {"narrator": "xtts-v2"}

    await run_synthesize(client, "test-job-synth", segments, voice_mapping, engine_mapping)

    status = _read_status("test-job-synth")
    assert status["phase"] == "done"
    assert status["status"] == "done"
    assert status["output_file"] == "chapter_test.wav"
    assert len(status["clips"]) == 1

    await client.aclose()
