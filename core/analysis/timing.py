"""Timing concerns for the analysis pipeline.

Measuring nodes used to be a decorator each node function opted into. It now
belongs to the pipeline runner, which is the thing that knows a node ran: a
node should not have to ask to be measured, and doing it in one place means a
node added later is timed without doing anything. What is left here is how a
duration is presented.
"""

from __future__ import annotations


def format_duration(ms: int) -> str:
    """Render milliseconds the way the report shows them."""
    if ms < 1000:
        return f"{ms}ms"
    total_seconds = ms // 1000
    if total_seconds < 60:
        return f"{total_seconds}s"
    minutes = total_seconds // 60
    seconds = total_seconds % 60
    return f"{minutes}m {seconds}s"
