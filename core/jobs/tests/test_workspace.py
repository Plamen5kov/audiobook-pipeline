"""Tests for the job workspace and the reuse decision.

The reuse decision is the one that can quietly ruin a chapter: reuse a clip
that should have been re-rendered and the audio no longer matches the text,
with nothing in the output to say so.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.jobs.fingerprint import fingerprint, plan, segment_fingerprint
from core.jobs.workspace import STAGES, Workspace, stage_dirname


@pytest.fixture
def job():
    with tempfile.TemporaryDirectory() as tmp:
        yield Workspace(Path(tmp)).job("ch1001", create=True)


def seg(i=1, text="He walked in.", **kw):
    return {"id": i, "spoken_text": text, "speaker": "narrator",
            "emotion": "neutral", "intensity": 0.5, **kw}


def test_stage_directories_are_numbered_in_pipeline_order():
    assert stage_dirname("input") == "00-input"
    assert stage_dirname("analysis") == "01-analysis"
    assert stage_dirname("qa") == f"{len(STAGES) - 1:02d}-qa"


def test_unknown_stage_is_rejected():
    with pytest.raises(KeyError):
        stage_dirname("nonsense")


def test_artifacts_land_in_their_stage_directory(job):
    p = job.write_json("analysis", "segments.json", [seg()])
    assert p.parent.name == "01-analysis"
    assert job.read_json("analysis", "segments.json")[0]["id"] == 1


def test_reading_a_missing_artifact_gives_none(job):
    assert job.read_json("analysis", "segments.json") is None


def test_manifest_survives_a_reopen(job):
    job.record_stage("analysis", "done", artifact="segments.json")
    reopened = Workspace(job.root.parent).job("ch1001")
    assert reopened.stage_status("analysis") == "done"
    assert reopened.manifest()["stages"]["analysis"]["artifact"] == "01-analysis/segments.json"


def test_a_stage_never_run_is_distinguishable_from_a_failed_one(job):
    assert job.stage_status("qa") is None
    job.record_stage("qa", "failed")
    assert job.stage_status("qa") == "failed"


def test_summary_lists_every_stage(job):
    job.record_stage("analysis", "done")
    s = job.summary()
    assert s["stages"]["analysis"] == "done"
    assert s["stages"]["assembly"] == "not run"


def test_manifest_write_is_atomic(job):
    job.record_stage("analysis", "done")
    # No temp files left behind, and the manifest parses.
    assert not list(job.root.glob("*.tmp"))
    json.loads(job.manifest_path.read_text())


def test_unsafe_job_ids_are_refused():
    with tempfile.TemporaryDirectory() as tmp:
        ws = Workspace(Path(tmp))
        for bad in ("../escape", "a/b"):
            with pytest.raises(ValueError):
                ws.job(bad)


# ---- the reuse decision ---------------------------------------------------


def test_same_inputs_give_the_same_fingerprint():
    assert segment_fingerprint(seg()) == segment_fingerprint(seg())


def test_changed_text_changes_the_fingerprint():
    assert segment_fingerprint(seg()) != segment_fingerprint(seg(text="He ran in."))


def test_changed_voice_changes_the_fingerprint():
    assert segment_fingerprint(seg(), voice="Dylan") != segment_fingerprint(seg(), voice="Ryan")


def test_changed_emotion_changes_the_fingerprint():
    assert segment_fingerprint(seg()) != segment_fingerprint(seg(emotion="angry"))


def test_fields_outside_the_contract_do_not_matter():
    # Attribution source is bookkeeping, not delivery.
    assert segment_fingerprint(seg()) == segment_fingerprint(seg(attribution_source="ai"))


def _voice_of(_):
    return "Ryan"


def _engine_of(_):
    return "qwen3-tts"


def _render(job, segment, name="0001.wav"):
    """Pretend a clip was synthesised and recorded."""
    clip = job.path("synthesis", name, create=True)
    clip.write_bytes(b"RIFF")
    rel = f"{stage_dirname('synthesis')}/{name}"
    job.record_segment(segment["id"], segment_fingerprint(segment, _voice_of(segment),
                                                          _engine_of(segment)), rel)
    job.save()
    return rel


def test_everything_needs_rendering_on_a_fresh_job(job):
    todo, reuse = plan([seg(1), seg(2, "And then he left.")], job, _voice_of, _engine_of)
    assert len(todo) == 2 and reuse == []


def test_an_unchanged_segment_is_reused(job):
    s = seg(1)
    _render(job, s)
    todo, reuse = plan([s], job, _voice_of, _engine_of)
    assert todo == [] and len(reuse) == 1
    assert reuse[0]["_clip"].endswith("0001.wav")


def test_only_the_changed_segment_is_rendered_again(job):
    a, b = seg(1), seg(2, "And then he left.")
    _render(job, a, "0001.wav")
    _render(job, b, "0002.wav")
    edited = seg(2, "And then he ran out.")
    todo, reuse = plan([a, edited], job, _voice_of, _engine_of)
    assert [t["id"] for t in todo] == [2]
    assert [r["id"] for r in reuse] == [1]


def test_a_deleted_clip_is_rendered_again_even_though_the_manifest_says_done(job):
    s = seg(1)
    _render(job, s)
    (job.root / stage_dirname("synthesis") / "0001.wav").unlink()
    todo, reuse = plan([s], job, _voice_of, _engine_of)
    assert [t["id"] for t in todo] == [1] and reuse == []


def test_forcing_a_segment_renders_it_again(job):
    s = seg(1)
    _render(job, s)
    todo, reuse = plan([s], job, _voice_of, _engine_of, force={1})
    assert [t["id"] for t in todo] == [1] and reuse == []


def test_forgetting_a_segment_renders_it_again(job):
    s = seg(1)
    _render(job, s)
    job.forget_segment(1)
    todo, _ = plan([s], job, _voice_of, _engine_of)
    assert [t["id"] for t in todo] == [1]


def test_a_changed_voice_alone_forces_a_rerender(job):
    s = seg(1)
    _render(job, s)
    todo, reuse = plan([s], job, lambda _: "Dylan", _engine_of)
    assert [t["id"] for t in todo] == [1] and reuse == []


def test_fingerprint_ignores_absent_optional_fields():
    assert fingerprint("hello") == fingerprint("hello", speed=None)


def test_an_absolute_clip_path_is_checked_where_it_actually_is(job, tmp_path):
    """The synthesiser writes into a volume shared with assembly, not into the
    job directory, so the manifest holds an absolute path."""
    clip = tmp_path / "seg0001.wav"
    clip.write_bytes(b"RIFF")
    s = seg(1)
    job.record_segment(1, segment_fingerprint(s, _voice_of(s), _engine_of(s)), str(clip))
    job.save()

    todo, reuse = plan([s], job, _voice_of, _engine_of)
    assert todo == [] and len(reuse) == 1

    clip.unlink()
    todo, reuse = plan([s], job, _voice_of, _engine_of)
    assert [t["id"] for t in todo] == [1] and reuse == []
