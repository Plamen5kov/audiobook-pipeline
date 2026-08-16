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


# ---------------------------------------------------------------------------
# Artifacts and selective re-rendering
# ---------------------------------------------------------------------------


class CountingTransport(MockTransport):
    """Mock transport that records how many segments were synthesised."""

    def __init__(self, responses: dict[str, dict]):
        super().__init__(responses)
        self.synthesised: list[int] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if "/synthesize" in str(request.url):
            body = json.loads(request.content)
            seg_id = body["segment_id"]
            self.synthesised.append(seg_id)
            path = os.path.join(_tmpdir, f"seg{seg_id:04d}.wav")
            with open(path, "wb") as fh:
                fh.write(b"RIFF")
            return httpx.Response(200, json={
                "segment_id": seg_id, "speaker": body["speaker"],
                "file_path": path, "filename": os.path.basename(path),
            })
        return await super().handle_async_request(request)


def _segments(second_text="And then he left."):
    return [
        {"id": 1, "speaker": "narrator", "spoken_text": "He walked in.",
         "emotion": "neutral", "intensity": 0.5, "pause_before_ms": 0},
        {"id": 2, "speaker": "narrator", "spoken_text": second_text,
         "emotion": "neutral", "intensity": 0.5, "pause_before_ms": 0},
    ]


def _job(job_id):
    from pathlib import Path

    from app.orchestrator import WORKSPACE_DIR
    from core.jobs.workspace import Workspace

    return Workspace(Path(WORKSPACE_DIR)).job(job_id)


def _transport():
    return CountingTransport({
        "/assemble": {"json": {"filename": "chapter_test.mp3", "duration_ms": 5000}},
    })


@pytest.mark.asyncio
async def test_analysis_is_kept_as_an_artifact():
    transport = MockTransport({
        "/analyze": {"json": {
            "title": "Test",
            "segments": [{"id": 1, "speaker": "narrator", "original_text": "Hello"}],
            "characters": [{"name": "narrator"}],
            "report": {},
        }}
    })
    client = httpx.AsyncClient(transport=transport)
    await run_analyze(client, "job-artifacts", "Test Title", "Hello world")
    await client.aclose()

    job = _job("job-artifacts")
    assert job.stage_status("analysis") == "done"
    assert job.read_json("analysis", "segments.json")["segments"][0]["id"] == 1
    assert (job.stage_dir("input") / "chapter.txt").read_text() == "Hello world"


@pytest.mark.asyncio
async def test_a_failed_analysis_is_recorded_as_failed():
    transport = MockTransport({"/analyze": {"status": 500, "json": {"detail": "boom"}}})
    client = httpx.AsyncClient(transport=transport)
    await run_analyze(client, "job-failed", "Title", "Text")
    await client.aclose()

    assert _job("job-failed").stage_status("analysis") == "failed"


@pytest.mark.asyncio
async def test_second_run_renders_only_the_changed_segment():
    transport = _transport()
    client = httpx.AsyncClient(transport=transport)
    args = ({"narrator": "Ryan"}, {"narrator": "qwen3-tts"})

    await run_synthesize(client, "job-reuse", _segments(), *args)
    assert sorted(transport.synthesised) == [1, 2]

    transport.synthesised.clear()
    await run_synthesize(client, "job-reuse", _segments("And then he ran out."), *args)
    assert transport.synthesised == [2], "only the edited line should be rendered"

    await client.aclose()
    status = _read_status("job-reuse")
    # Assembly still receives the whole chapter, in order.
    assert [c["id"] for c in status["clips"]] == [1, 2]


@pytest.mark.asyncio
async def test_an_unchanged_rerun_renders_nothing():
    transport = _transport()
    client = httpx.AsyncClient(transport=transport)
    args = ({"narrator": "Ryan"}, {"narrator": "qwen3-tts"})

    await run_synthesize(client, "job-nochange", _segments(), *args)
    transport.synthesised.clear()
    await run_synthesize(client, "job-nochange", _segments(), *args)

    assert transport.synthesised == []
    status = _read_status("job-nochange")
    assert len(status["clips"]) == 2
    await client.aclose()


@pytest.mark.asyncio
async def test_forcing_a_segment_renders_it_again():
    transport = _transport()
    client = httpx.AsyncClient(transport=transport)
    args = ({"narrator": "Ryan"}, {"narrator": "qwen3-tts"})

    await run_synthesize(client, "job-force", _segments(), *args)
    transport.synthesised.clear()
    await run_synthesize(client, "job-force", _segments(), *args, force={2})

    assert transport.synthesised == [2]
    await client.aclose()


@pytest.mark.asyncio
async def test_changing_the_voice_rerenders_everything_it_affects():
    transport = _transport()
    client = httpx.AsyncClient(transport=transport)

    await run_synthesize(client, "job-voice", _segments(),
                         {"narrator": "Ryan"}, {"narrator": "qwen3-tts"})
    transport.synthesised.clear()
    await run_synthesize(client, "job-voice", _segments(),
                         {"narrator": "Dylan"}, {"narrator": "qwen3-tts"})

    assert sorted(transport.synthesised) == [1, 2]
    await client.aclose()


@pytest.mark.asyncio
async def test_cast_and_qa_are_kept_as_artifacts():
    transport = _transport()
    client = httpx.AsyncClient(transport=transport)
    await run_synthesize(client, "job-stages", _segments(),
                         {"narrator": "Ryan"}, {"narrator": "qwen3-tts"})
    await client.aclose()

    job = _job("job-stages")
    assert job.stage_status("cast") == "done"
    assert job.read_json("cast", "cast.json")["voice_mapping"]["narrator"] == "Ryan"
    assert job.stage_status("assembly") == "done"
    assert job.manifest()["stages"]["synthesis"]["rendered"] == 2
