"""Tests for the pipeline machinery: registry, ordering checks, editing, runs.

These test the frame rather than the analysis. What matters is that a node can
be put somewhere new without anything else changing, and that a pipeline which
cannot work says so before it processes a chapter instead of failing part-way
through one.
"""

import asyncio

import pytest

from app.models import AnalysisContext
from app.nodes.base import Node, create, register, registered
from app.pipeline import DEFAULT_PIPELINE, Pipeline, PipelineOrderError, run_analysis

TEXT = (
    "Jason looked at the door.\n\n"
    "“That is a door,” he said.\n\n"
    "Rufus shook his head.\n\n"
    "“It is a wall,” Rufus replied.\n"
)

WITH_LLM = ("text", "title", "llm")
DETERMINISTIC = tuple(n for n in DEFAULT_PIPELINE
                      if n not in ("ai_attribution", "emotion_classifier"))


class RecordingNode(Node):
    """A node that records that it ran, for order assertions."""

    name = "recording"
    requires = ()
    assigns = ("recorded",)

    async def run(self, ctx):
        ctx.meta.setdefault("ran", []).append(self.name)


class NeedsEverything(Node):
    name = "needs_everything"
    requires = ("segments", "characters", "nothing_provides_this")
    assigns = ()

    async def run(self, ctx):
        pass


def test_every_default_node_is_registered():
    for name in DEFAULT_PIPELINE:
        assert name in registered()


def test_create_rejects_an_unknown_node():
    with pytest.raises(KeyError):
        create("no_such_node")


def test_register_rejects_a_nameless_node():
    class Nameless(Node):
        async def run(self, ctx):
            pass

    with pytest.raises(ValueError):
        register(Nameless)


def test_default_pipeline_order_is_satisfiable():
    assert Pipeline.from_names().problems(WITH_LLM) == []


def test_ai_nodes_are_reported_when_there_is_no_model():
    problems = Pipeline.from_names().problems(("text", "title"))
    assert len(problems) == 2
    assert all("llm" in p for p in problems)


def test_deterministic_subset_needs_no_model():
    assert Pipeline.from_names(DETERMINISTIC).problems(("text", "title")) == []


def test_out_of_order_pipeline_is_rejected():
    p = Pipeline.from_names(("explicit_attribution", "segment_splitter"))
    with pytest.raises(PipelineOrderError) as exc:
        p.validate()
    assert "explicit_attribution requires segments" in str(exc.value)


def test_unsatisfiable_requirement_names_itself():
    p = Pipeline.from_names(DETERMINISTIC).append(NeedsEverything())
    problems = p.problems(("text", "title"))
    assert problems == [
        "needs_everything requires nothing_provides_this, "
        "which nothing before it provides"
    ]


def test_insert_after_places_the_node():
    p = Pipeline.from_names(DETERMINISTIC).insert_after("pause_timing", RecordingNode())
    assert p.names.index("recording") == p.names.index("pause_timing") + 1


def test_insert_before_places_the_node():
    p = Pipeline.from_names(DETERMINISTIC).insert_before("validation", RecordingNode())
    assert p.names.index("recording") == p.names.index("validation") - 1


def test_replace_swaps_in_place():
    p = Pipeline.from_names(DETERMINISTIC)
    at = p.index_of("pause_timing")
    p.replace("pause_timing", RecordingNode())
    assert p.names[at] == "recording"
    assert "pause_timing" not in p.names


def test_remove_drops_the_node():
    p = Pipeline.from_names(DETERMINISTIC).remove("validation")
    assert "validation" not in p.names


def test_editing_an_absent_node_is_an_error():
    with pytest.raises(KeyError):
        Pipeline.from_names(DETERMINISTIC).insert_after("nope", RecordingNode())


def test_nodes_run_in_order_and_are_timed():
    p = Pipeline([RecordingNode(), RecordingNode()])
    ctx = AnalysisContext(text="x")
    metrics = asyncio.run(p.run(ctx))
    assert ctx.meta["ran"] == ["recording", "recording"]
    assert [m.node_name for m in metrics] == ["recording", "recording"]
    assert all(m.duration_ms >= 0 for m in metrics)


def test_deterministic_run_produces_segments_and_cast():
    ctx = AnalysisContext(text=TEXT, title="ch1")
    result = asyncio.run(run_analysis(ctx, Pipeline.from_names(DETERMINISTIC)))

    assert result.title == "ch1"
    assert len(result.segments) > 0
    assert {s["speaker"] for s in result.segments} >= {"narrator"}
    assert any(c["name"] == "narrator" for c in result.characters)
    # Every node that ran is timed and typed in the report.
    assert [n["node"] for n in result.report["nodes"]] == list(DETERMINISTIC)


def test_validation_runs_and_passes_on_untouched_text():
    ctx = AnalysisContext(text=TEXT, title="ch1")
    asyncio.run(run_analysis(ctx, Pipeline.from_names(DETERMINISTIC)))
    assert ctx.validation is not None
    assert ctx.validation["passed"], ctx.validation["issues"]


def test_a_pipeline_without_a_model_refuses_the_ai_nodes():
    ctx = AnalysisContext(text=TEXT, title="ch1")
    with pytest.raises(PipelineOrderError):
        asyncio.run(run_analysis(ctx, Pipeline.from_names()))


class ScriptedLLM:
    """An ``LLMClient`` with no server behind it.

    It satisfies the protocol structurally, without importing it, which is the
    reason the AI nodes take a client instead of a URL: the full pipeline runs
    here with no Ollama, no httpx and no network.
    """

    def __init__(self, reply: dict):
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        self.calls.append((system_prompt, user_prompt))
        return self.reply


def test_full_pipeline_runs_against_a_fake_model():
    llm = ScriptedLLM({"attributions": [], "emotions": []})
    ctx = AnalysisContext(text=TEXT, title="ch1", llm=llm)
    result = asyncio.run(run_analysis(ctx))

    assert [n["node"] for n in result.report["nodes"]] == list(DEFAULT_PIPELINE)
    assert llm.calls, "the AI nodes should have consulted the model"
    assert result.report["ai_duration_ms"] >= 0


def test_narration_is_forced_neutral_whatever_the_model_says():
    llm = ScriptedLLM({
        "attributions": [],
        "emotions": [{"id": i, "emotion": "angry", "intensity": 0.9}
                     for i in range(1, 12)],
    })
    ctx = AnalysisContext(text=TEXT, title="ch1", llm=llm)
    result = asyncio.run(run_analysis(ctx))

    narration = [s for s in result.segments if s["speaker"] == "narrator"]
    assert narration, "the sample text has narration"
    assert all(s["emotion"] == "neutral" for s in narration)


def test_a_model_that_fails_does_not_stop_the_run():
    class Broken:
        async def complete_json(self, system_prompt, user_prompt):
            raise RuntimeError("model is down")

    ctx = AnalysisContext(text=TEXT, title="ch1", llm=Broken())
    result = asyncio.run(run_analysis(ctx))
    assert len(result.segments) > 0


def test_inserted_node_sees_and_changes_the_shared_context():
    """The point of the context: a new node reads and writes without any
    other node's signature changing."""

    class Tagger(Node):
        name = "tagger"
        requires = ("segments",)
        assigns = ("segments.tagged",)

        async def run(self, ctx):
            for s in ctx.segments:
                ctx.meta.setdefault("tagged", []).append(s.id)

    ctx = AnalysisContext(text=TEXT, title="ch1")
    p = Pipeline.from_names(DETERMINISTIC).insert_after("segment_splitter", Tagger())
    asyncio.run(run_analysis(ctx, p))
    assert len(ctx.meta["tagged"]) == len(ctx.segments)
