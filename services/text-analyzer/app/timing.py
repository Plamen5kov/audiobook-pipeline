"""Transparent timing for pipeline nodes.

Provides a ``@timed_node`` decorator and a ``collect_metrics()`` context
manager.  Together they let ``pipeline.py`` remain pure business logic
while every decorated node function automatically records its duration.

Usage in a node module::

    from ..timing import timed_node

    @timed_node("segment_splitter", "programmatic")
    def split_segments(text: str) -> list[Segment]:
        ...

Usage in the pipeline::

    async def run_pipeline(...):
        with collect_metrics() as metrics:
            segments = segment_splitter.split_segments(text)
            segments = await emotion_classifier.classify_emotions(...)
        report = _build_report(metrics)
"""

from __future__ import annotations

import asyncio
import contextvars
import functools
import logging
import time

from .models import NodeMetrics

log = logging.getLogger(__name__)

_current_metrics: contextvars.ContextVar[list[NodeMetrics] | None] = (
    contextvars.ContextVar("_current_metrics", default=None)
)


class collect_metrics:
    """Context manager that activates metric collection for ``@timed_node``.

    Yields a ``list[NodeMetrics]`` that decorated functions append to
    automatically.
    """

    def __enter__(self) -> list[NodeMetrics]:
        self._metrics: list[NodeMetrics] = []
        self._token = _current_metrics.set(self._metrics)
        return self._metrics

    def __exit__(self, *exc) -> None:
        _current_metrics.reset(self._token)


def timed_node(name: str, node_type: str):
    """Decorator that records duration of a pipeline node.

    Works with both sync and async functions.  If no ``collect_metrics``
    context is active the function executes normally without recording.
    """

    def decorator(fn):
        if asyncio.iscoroutinefunction(fn):

            @functools.wraps(fn)
            async def wrapper(*args, **kwargs):
                metrics = _current_metrics.get(None)
                t0 = time.monotonic_ns()
                result = await fn(*args, **kwargs)
                _record(metrics, name, node_type, t0)
                return result

        else:

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                metrics = _current_metrics.get(None)
                t0 = time.monotonic_ns()
                result = fn(*args, **kwargs)
                _record(metrics, name, node_type, t0)
                return result

        return wrapper

    return decorator


def _record(
    metrics: list[NodeMetrics] | None,
    name: str,
    node_type: str,
    t0: int,
) -> None:
    """Compute duration and append to the metrics list (if active)."""
    duration_ms = (time.monotonic_ns() - t0) // 1_000_000
    log.info("%s: %d ms", name, duration_ms)
    if metrics is not None:
        metrics.append(NodeMetrics(name, node_type, duration_ms))
