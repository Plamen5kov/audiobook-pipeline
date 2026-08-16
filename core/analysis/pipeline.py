"""The analysis pipeline: an ordered list of nodes and a runner for it.

The pipeline holds nodes, times them, and hands each one the shared context.
It builds no clients and reads no configuration: whoever runs it supplies a
prepared context, which is what keeps this importable by a service, by the
corpus builder and by a script alike.
It knows nothing about what any node does, so adding a step is a change to
``DEFAULT_PIPELINE`` or a call to ``insert_after``, never a change here.

Order is checked before anything runs. Each node declares what it needs and
what it produces, so a pipeline that puts the emotion classifier before the
splitter is rejected with a readable reason rather than failing part-way
through a chapter. The check trusts those declarations: a node that lies about
what it assigns will pass the check and still be wrong.
"""

from __future__ import annotations

import logging
import time
from typing import Iterable, Sequence

from .models import AnalysisContext, NodeMetrics, PipelineResult
from .timing import format_duration
# Importing the package registers every node in it; see nodes/__init__.py.
from .nodes.base import SEED_KEYS, Node, create

log = logging.getLogger(__name__)

# The analysis, in order. This list is the pipeline's definition: to add a step,
# put its registered name where it belongs.
DEFAULT_PIPELINE: tuple[str, ...] = (
    "segment_splitter",
    "explicit_attribution",
    "turn_taking",
    "character_registry",
    "pause_timing",
    "validation",
    "ai_attribution",
    "emotion_classifier",
    "narration_defaults",
    "normalisation",
)


class PipelineOrderError(ValueError):
    """A pipeline was assembled in an order that cannot work."""


class Pipeline:
    """An ordered, editable list of nodes."""

    def __init__(self, nodes: Sequence[Node]):
        self._nodes: list[Node] = list(nodes)

    @classmethod
    def from_names(cls, names: Iterable[str] = DEFAULT_PIPELINE) -> "Pipeline":
        return cls([create(n) for n in names])

    @property
    def names(self) -> list[str]:
        return [n.name for n in self._nodes]

    def __iter__(self):
        return iter(self._nodes)

    def __len__(self) -> int:
        return len(self._nodes)

    def index_of(self, name: str) -> int:
        for i, node in enumerate(self._nodes):
            if node.name == name:
                return i
        raise KeyError(f"no node named {name!r} in this pipeline: {self.names}")

    def append(self, node: Node) -> "Pipeline":
        self._nodes.append(node)
        return self

    def insert_before(self, name: str, node: Node) -> "Pipeline":
        self._nodes.insert(self.index_of(name), node)
        return self

    def insert_after(self, name: str, node: Node) -> "Pipeline":
        self._nodes.insert(self.index_of(name) + 1, node)
        return self

    def replace(self, name: str, node: Node) -> "Pipeline":
        self._nodes[self.index_of(name)] = node
        return self

    def remove(self, name: str) -> "Pipeline":
        del self._nodes[self.index_of(name)]
        return self

    def problems(self, seed: Iterable[str] = SEED_KEYS) -> list[str]:
        """Ordering problems, in the order they would be hit.

        A requirement is met when an earlier node assigns it or the context
        seeded it. Keys match literally, so a node needing ``segments.speaker``
        must follow one that assigns exactly that: holding ``segments`` is not
        enough, which is the point, since an unattributed segment list will not
        do.
        """
        available = set(seed)
        issues: list[str] = []
        for node in self._nodes:
            missing = [r for r in node.requires if r not in available]
            if missing:
                issues.append(
                    f"{node.name} requires {', '.join(missing)}, "
                    f"which nothing before it provides")
            available.update(node.assigns)
        return issues

    def validate(self, seed: Iterable[str] = SEED_KEYS) -> None:
        issues = self.problems(seed)
        if issues:
            raise PipelineOrderError("; ".join(issues))

    async def run(self, ctx: AnalysisContext) -> list[NodeMetrics]:
        """Run every node in order against *ctx*, returning per-node timings."""
        metrics: list[NodeMetrics] = []
        for node in self._nodes:
            t0 = time.monotonic_ns()
            try:
                await node.run(ctx)
            except Exception:
                # Which node failed is the first thing anyone needs, and the
                # traceback alone points at the pure function, not the step.
                log.exception("node %s failed after %d nodes", node.name, len(metrics))
                raise
            duration_ms = (time.monotonic_ns() - t0) // 1_000_000
            log.info("%s: %d ms", node.name, duration_ms)
            metrics.append(NodeMetrics(node.name, node.node_type, duration_ms))
        return metrics


def seed_keys(ctx: AnalysisContext) -> tuple[str, ...]:
    """What the context already provides before any node has run."""
    return SEED_KEYS + (("llm",) if ctx.llm is not None else ())


async def run_analysis(ctx: AnalysisContext,
                       pipeline: Pipeline | None = None) -> PipelineResult:
    """Run a pipeline over a prepared context and format the result."""
    pipeline = pipeline or Pipeline.from_names()
    pipeline.validate(seed_keys(ctx))
    metrics = await pipeline.run(ctx)

    output_segments = [
        {
            "id": s.id,
            "speaker": s.speaker if s.kind == "dialogue" else "narrator",
            "original_text": s.original_text,
            "spoken_text": s.spoken_text or s.original_text,
            "emotion": s.emotion,
            "intensity": round(s.intensity, 2),
            "pause_before_ms": s.pause_before_ms,
        }
        for s in ctx.segments
    ]

    report = _build_report(metrics)
    if ctx.validation and not ctx.validation["passed"]:
        report["validation"] = ctx.validation

    log.info(
        "Pipeline complete: %d segments, %d characters | "
        "total=%s (programmatic=%s, ai=%s)",
        len(output_segments), len(ctx.characters),
        report["total_duration"],
        report["programmatic_duration"],
        report["ai_duration"],
    )

    return PipelineResult(
        title=ctx.title,
        characters=ctx.characters,
        segments=output_segments,
        report=report,
    )





def _build_report(metrics: list[NodeMetrics]) -> dict:
    """Build the structured report dict from node metrics."""
    total_ms = sum(m.duration_ms for m in metrics)
    prog_ms = sum(m.duration_ms for m in metrics if m.node_type == "programmatic")
    ai_ms = sum(m.duration_ms for m in metrics if m.node_type == "ai")

    return {
        "total_duration_ms": total_ms,
        "total_duration": format_duration(total_ms),
        "programmatic_duration_ms": prog_ms,
        "programmatic_duration": format_duration(prog_ms),
        "ai_duration_ms": ai_ms,
        "ai_duration": format_duration(ai_ms),
        "nodes": [
            {
                "node": m.node_name,
                "type": m.node_type,
                "duration_ms": m.duration_ms,
                "duration": format_duration(m.duration_ms),
            }
            for m in metrics
        ],
    }
