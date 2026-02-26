"""Pipeline orchestrator.

Runs the 8-node hybrid analysis pipeline in sequence.  Timing is handled
transparently by the ``@timed_node`` decorators on each node function —
this module contains only business logic.
"""

from __future__ import annotations

import logging

from .models import NodeMetrics, PipelineResult
from .timing import collect_metrics
from .nodes import (
    segment_splitter,
    explicit_attribution,
    turn_taking,
    character_registry,
    pause_timing,
    validation,
    ai_attribution,
    emotion_classifier,
)

log = logging.getLogger(__name__)


async def run_pipeline(
    text: str,
    title: str,
    ollama_url: str,
    model_name: str,
) -> PipelineResult:
    """Run the full 8-node analysis pipeline and return structured results."""

    with collect_metrics() as metrics:
        segments = segment_splitter.split_segments(text)
        segments = explicit_attribution.attribute_explicit(segments)
        segments = turn_taking.apply_turn_taking(segments)
        characters = character_registry.build_character_registry(segments)
        segments = pause_timing.assign_pauses(segments)
        validation.validate_completeness(segments, text)

        segments = await ai_attribution.resolve_ambiguous_speakers(
            segments, characters, ollama_url, model_name,
        )
        segments = await emotion_classifier.classify_emotions(
            segments, ollama_url, model_name,
        )

    # Post-processing overrides.
    for s in segments:
        if s.kind == "narration":
            s.emotion = "neutral"
            s.intensity = 0.5
        elif s.kind == "dialogue" and s.original_text.rstrip().endswith("?"):
            s.emotion = "curious"

    output_segments = [
        {
            "id": s.id,
            "speaker": s.speaker if s.kind == "dialogue" else "narrator",
            "original_text": s.original_text,
            "emotion": s.emotion,
            "intensity": round(s.intensity, 2),
            "pause_before_ms": s.pause_before_ms,
        }
        for s in segments
    ]

    report = _build_report(metrics)

    log.info(
        "Pipeline complete: %d segments, %d characters | "
        "total=%s (programmatic=%s, ai=%s)",
        len(output_segments), len(characters),
        report["total_duration"],
        report["programmatic_duration"],
        report["ai_duration"],
    )

    return PipelineResult(
        title=title,
        characters=characters,
        segments=output_segments,
        report=report,
    )


def _format_duration(ms: int) -> str:
    """Format milliseconds as a human-readable duration."""
    if ms < 1000:
        return f"{ms}ms"
    total_seconds = ms // 1000
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m {seconds}s"


def _build_report(metrics: list[NodeMetrics]) -> dict:
    """Build the structured report dict from node metrics."""
    total_ms = sum(m.duration_ms for m in metrics)
    prog_ms = sum(m.duration_ms for m in metrics if m.node_type == "programmatic")
    ai_ms = sum(m.duration_ms for m in metrics if m.node_type == "ai")

    return {
        "total_duration_ms": total_ms,
        "total_duration": _format_duration(total_ms),
        "programmatic_duration_ms": prog_ms,
        "programmatic_duration": _format_duration(prog_ms),
        "ai_duration_ms": ai_ms,
        "ai_duration": _format_duration(ai_ms),
        "nodes": [
            {
                "node": m.node_name,
                "type": m.node_type,
                "duration_ms": m.duration_ms,
                "duration": _format_duration(m.duration_ms),
            }
            for m in metrics
        ],
    }
