"""Pipeline orchestrator.

Runs the 8-node hybrid analysis pipeline in sequence, collecting
per-node timing metrics into a structured report.
"""

from __future__ import annotations

import logging
import time

from .models import NodeMetrics, PipelineResult, Segment
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
    """Run the full 8-node analysis pipeline and return structured results
    with a per-node metrics report."""

    metrics: list[NodeMetrics] = []
    segments: list[Segment] = []

    # ------------------------------------------------------------------
    # Node 1 — Segment Splitter (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    segments = segment_splitter.split_segments(text)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    metrics.append(NodeMetrics(
        "segment_splitter", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=len(segments),
    ))
    log.info("Node 1 (segment_splitter): %d segments in %d ms", len(segments), dt)

    # ------------------------------------------------------------------
    # Node 2 — Explicit Attribution (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    unknown_before = _count_unknown(segments)
    segments = explicit_attribution.attribute_explicit(segments)
    unknown_after = _count_unknown(segments)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    resolved = unknown_before - unknown_after
    metrics.append(NodeMetrics(
        "explicit_attribution", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=resolved,
    ))
    log.info("Node 2 (explicit_attribution): resolved %d/%d in %d ms",
             resolved, unknown_before, dt)

    # ------------------------------------------------------------------
    # Node 3 — Turn-Taking (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    unknown_before = _count_unknown(segments)
    segments = turn_taking.apply_turn_taking(segments)
    unknown_after = _count_unknown(segments)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    resolved = unknown_before - unknown_after
    metrics.append(NodeMetrics(
        "turn_taking", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=resolved,
    ))
    log.info("Node 3 (turn_taking): resolved %d/%d in %d ms",
             resolved, unknown_before, dt)

    # ------------------------------------------------------------------
    # Node 4 — Character Registry (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    characters = character_registry.build_character_registry(segments)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    metrics.append(NodeMetrics(
        "character_registry", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=len(characters),
    ))
    log.info("Node 4 (character_registry): %d characters in %d ms",
             len(characters), dt)

    # ------------------------------------------------------------------
    # Node 5 — Pause/Timing (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    segments = pause_timing.assign_pauses(segments)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    metrics.append(NodeMetrics(
        "pause_timing", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=len(segments),
    ))
    log.info("Node 5 (pause_timing): %d segments in %d ms", len(segments), dt)

    # ------------------------------------------------------------------
    # Node 6 — Validation (programmatic)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    passed, issues = validation.validate_completeness(segments, text)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    metrics.append(NodeMetrics(
        "validation", "programmatic", dt,
        segments_processed=len(segments),
        segments_affected=0 if passed else len(issues),
    ))
    log.info("Node 6 (validation): passed=%s issues=%d in %d ms",
             passed, len(issues), dt)

    # ------------------------------------------------------------------
    # Node 7 — AI Attribution (ai)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    unknown_before = _count_unknown(segments)
    segments = await ai_attribution.resolve_ambiguous_speakers(
        segments, characters, ollama_url, model_name,
    )
    unknown_after = _count_unknown(segments)
    dt = (time.monotonic_ns() - t0) // 1_000_000
    resolved = unknown_before - unknown_after
    metrics.append(NodeMetrics(
        "ai_attribution", "ai", dt,
        segments_processed=unknown_before,
        segments_affected=resolved,
    ))
    log.info("Node 7 (ai_attribution): resolved %d/%d in %d ms",
             resolved, unknown_before, dt)

    # ------------------------------------------------------------------
    # Node 8 — Emotion Classification (ai)
    # ------------------------------------------------------------------
    t0 = time.monotonic_ns()
    segments = await emotion_classifier.classify_emotions(
        segments, ollama_url, model_name,
    )
    dt = (time.monotonic_ns() - t0) // 1_000_000
    non_neutral = sum(1 for s in segments if s.emotion != "neutral")
    metrics.append(NodeMetrics(
        "emotion_classifier", "ai", dt,
        segments_processed=len(segments),
        segments_affected=non_neutral,
    ))
    log.info("Node 8 (emotion_classifier): %d segments, %d non-neutral in %d ms",
             len(segments), non_neutral, dt)

    # ------------------------------------------------------------------
    # Build output
    # ------------------------------------------------------------------
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

    # Log summary.
    log.info(
        "Pipeline complete: %d segments, %d characters | "
        "total=%dms (programmatic=%dms, ai=%dms)",
        len(output_segments), len(characters),
        report["total_duration_ms"],
        report["programmatic_duration_ms"],
        report["ai_duration_ms"],
    )

    return PipelineResult(
        title=title,
        characters=characters,
        segments=output_segments,
        report=report,
    )


def _count_unknown(segments: list[Segment]) -> int:
    return sum(1 for s in segments if s.kind == "dialogue" and s.speaker == "unknown")


def _build_report(metrics: list[NodeMetrics]) -> dict:
    """Build the structured report dict from node metrics."""
    total_ms = sum(m.duration_ms for m in metrics)
    prog_ms = sum(m.duration_ms for m in metrics if m.node_type == "programmatic")
    ai_ms = sum(m.duration_ms for m in metrics if m.node_type == "ai")

    return {
        "total_duration_ms": total_ms,
        "programmatic_duration_ms": prog_ms,
        "ai_duration_ms": ai_ms,
        "nodes": [
            {
                "node": m.node_name,
                "type": m.node_type,
                "duration_ms": m.duration_ms,
                "segments_processed": m.segments_processed,
                "segments_affected": m.segments_affected,
            }
            for m in metrics
        ],
    }
