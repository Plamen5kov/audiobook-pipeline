"""Tests for the workspace-reading endpoints.

Two things here matter beyond the happy path. The segments view joins three
files, so it has to stay right when one of them is missing. And serving a clip
follows a path out of the manifest, so it must refuse to leave the directories
this service is meant to read.
"""

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

_tmpdir = tempfile.mkdtemp()
os.environ["OUTPUT_DIR"] = _tmpdir
os.environ["WORKSPACE_DIR"] = os.path.join(_tmpdir, "workspace")

from app.config import workspace  # noqa: E402
from app.main import app  # noqa: E402
from core.jobs.fingerprint import segment_fingerprint  # noqa: E402
from core.jobs.workspace import stage_dirname  # noqa: E402

client = TestClient(app)

SEGMENTS = [
    {"id": 1, "kind": "narration", "speaker": "narrator",
     "original_text": "***\nHe walked in.", "spoken_text": "He walked in.",
     "emotion": "neutral", "intensity": 0.5, "pause_before_ms": 1000},
    {"id": 2, "kind": "dialogue", "speaker": "Michael",
     "original_text": "It is all quite Australian.",
     "spoken_text": "It is all quite Australian.",
     "emotion": "curious", "intensity": 0.6, "pause_before_ms": 250},
]


def _seed(job_id="ch1001", with_qa=True, with_clips=True):
    job = workspace().job(job_id, create=True)
    job.write_text("input", "chapter.txt", "chapter text")
    job.record_stage("input", "done", artifact="chapter.txt")
    job.write_json("analysis", "segments.json",
                   {"title": "It's All Quite Australian", "characters": [],
                    "segments": SEGMENTS})
    job.record_stage("analysis", "done", artifact="segments.json", segments=2)
    if with_clips:
        for seg in SEGMENTS:
            clip = job.path("synthesis", f"{seg['id']:04d}.wav", create=True)
            clip.write_bytes(b"RIFF0000")
            job.record_segment(seg["id"], segment_fingerprint(seg, "Ryan", "qwen3-tts"),
                               f"{stage_dirname('synthesis')}/{seg['id']:04d}.wav")
        job.record_stage("synthesis", "done", rendered=2, reused=0)
    if with_qa:
        job.write_json("qa", "report.json", {
            "status": "done", "failed_count": 1,
            "results": [{"id": 2, "status": "failed", "similarity": 0.61,
                         "heard": "it is all quite Austrian"}],
        })
        job.record_stage("qa", "done", artifact="report.json", failed=1)
    job.save()
    return job


@pytest.fixture(autouse=True)
def seeded():
    _seed()


def test_listing_runs_reports_how_far_each_got():
    r = client.get("/api/jobs")
    assert r.status_code == 200
    job = next(j for j in r.json() if j["job_id"] == "ch1001")
    assert job["stages"]["analysis"] == "done"
    assert job["stages"]["assembly"] == "not run"


def test_one_run_reports_its_stage_detail():
    r = client.get("/api/jobs/ch1001")
    assert r.status_code == 200
    assert r.json()["stage_detail"]["analysis"]["segments"] == 2
    assert r.json()["stage_detail"]["assembly"] is None


def test_unknown_run_is_a_404():
    assert client.get("/api/jobs/nope").status_code == 404


def test_a_traversing_job_id_is_refused():
    r = client.get("/api/jobs/..%2F..%2Fetc")
    assert r.status_code in (400, 404)


def test_a_stage_artifact_comes_back_as_written():
    r = client.get("/api/jobs/ch1001/stages/analysis")
    assert r.status_code == 200
    body = r.json()
    assert body["artifacts"]["segments.json"]["segments"][0]["id"] == 1
    assert body["status"] == "done"


def test_a_stage_that_ran_but_wrote_no_directory_is_not_a_404():
    """Synthesis and assembly record what they did but write their output to
    other volumes. Judging by directory alone called them never-run."""
    _seed("ch-assembled")
    job = workspace().job("ch-assembled")
    job.record_stage("assembly", "done", output="chapter.mp3", clips=2)

    r = client.get("/api/jobs/ch-assembled/stages/assembly")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "done"
    assert body["recorded"] == {"output": "chapter.mp3", "clips": 2}


def test_synthesis_reports_the_takes_from_the_ledger():
    r = client.get("/api/jobs/ch1001/stages/synthesis")
    assert r.status_code == 200
    clips = r.json()["clips"]
    assert [c["id"] for c in clips] == [1, 2]
    assert all(c["present"] for c in clips)


def test_a_stage_with_neither_a_record_nor_a_directory_is_still_a_404():
    _seed("ch-bare", with_qa=False, with_clips=False)
    assert client.get("/api/jobs/ch-bare/stages/assembly").status_code == 404


def test_an_audio_stage_lists_its_files():
    r = client.get("/api/jobs/ch1001/stages/synthesis")
    assert r.status_code == 200
    assert sorted(r.json()["files"]) == ["0001.wav", "0002.wav"]


def test_unknown_stage_is_rejected():
    assert client.get("/api/jobs/ch1001/stages/nonsense").status_code == 400


def test_a_stage_that_never_ran_is_a_404():
    assert client.get("/api/jobs/ch1001/stages/assembly").status_code == 404


def test_segments_join_analysis_clips_and_qa():
    r = client.get("/api/jobs/ch1001/segments")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 2 and body["returned"] == 2

    first, second = body["segments"]
    assert first["spoken_text"] == "He walked in."
    assert first["clip"]["present"] is True
    assert first["qa"] is None
    assert second["qa"]["status"] == "failed"
    assert second["clip"]["url"].endswith("/segments/2/audio")


def test_segments_can_be_narrowed_to_what_qa_flagged():
    body = client.get("/api/jobs/ch1001/segments?failed=true").json()
    assert [s["id"] for s in body["segments"]] == [2]
    assert body["total"] == 2 and body["returned"] == 1


def test_segments_can_be_narrowed_to_one_speaker():
    body = client.get("/api/jobs/ch1001/segments?speaker=Michael").json()
    assert [s["id"] for s in body["segments"]] == [2]


def test_segments_still_work_before_qa_has_run():
    _seed("ch-noqa", with_qa=False)
    body = client.get("/api/jobs/ch-noqa/segments").json()
    assert all(s["qa"] is None for s in body["segments"])


def test_a_missing_clip_is_reported_rather_than_assumed():
    job = _seed("ch-noclip", with_clips=False)
    body = client.get("/api/jobs/ch-noclip/segments").json()
    assert all(s["clip"]["present"] is False for s in body["segments"])
    assert job.stage_status("synthesis") is None


def test_segments_need_an_analysis():
    workspace().job("ch-empty", create=True)
    assert client.get("/api/jobs/ch-empty/segments").status_code == 404


def test_a_clip_can_be_played():
    r = client.get("/api/jobs/ch1001/segments/1/audio")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == b"RIFF0000"


def test_a_segment_with_no_clip_is_a_404():
    assert client.get("/api/jobs/ch1001/segments/99/audio").status_code == 404


def test_a_recorded_but_deleted_clip_is_a_404():
    job = workspace().job("ch1001")
    (job.root / stage_dirname("synthesis") / "0001.wav").unlink()
    assert client.get("/api/jobs/ch1001/segments/1/audio").status_code == 404


def test_a_clip_outside_the_allowed_roots_is_refused():
    """The manifest is ours, but a path is a path: serving one from anywhere
    would turn the manifest into a way to read arbitrary files."""
    job = workspace().job("ch1001")
    job.record_segment(1, "deadbeef", "/etc/hostname")
    job.save()
    r = client.get("/api/jobs/ch1001/segments/1/audio")
    assert r.status_code == 403


def test_redo_forgets_those_clips_only():
    job = _seed("ch-redo")
    r = client.post("/api/jobs/ch-redo/redo", json={"segments": [2]})
    assert r.status_code == 200
    assert r.json()["marked"] == [2]

    reopened = workspace().job("ch-redo")
    assert reopened.segment_record(2) is None
    assert reopened.segment_record(1) is not None


def test_redo_reports_segments_that_had_no_clip():
    _seed("ch-redo2")
    body = client.post("/api/jobs/ch-redo2/redo", json={"segments": [2, 99]}).json()
    assert body["had_no_clip"] == [99]


def test_redo_rejects_a_malformed_body():
    for bad in ({}, {"segments": []}, {"segments": "all"}, {"segments": [1, "two"]}):
        assert client.post("/api/jobs/ch1001/redo", json=bad).status_code == 400


def test_deleting_one_run_leaves_the_others():
    _seed("ch-del-a")
    _seed("ch-del-b")
    assert client.delete("/api/jobs/ch-del-a").status_code == 200
    remaining = [j["job_id"] for j in client.get("/api/jobs").json()]
    assert "ch-del-a" not in remaining
    assert "ch-del-b" in remaining


def test_deleting_a_run_that_is_not_there_is_a_404():
    assert client.delete("/api/jobs/never-was").status_code == 404


def test_clearing_everything_needs_confirmation():
    _seed("ch-keep")
    assert client.delete("/api/jobs").status_code == 400
    assert "ch-keep" in [j["job_id"] for j in client.get("/api/jobs").json()]


def test_clearing_everything_with_confirmation_empties_the_workspace():
    _seed("ch-x")
    _seed("ch-y")
    r = client.delete("/api/jobs?confirm=all")
    assert r.status_code == 200
    assert r.json()["count"] >= 2
    assert client.get("/api/jobs").json() == []
