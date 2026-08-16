"""Force the narrator to a neutral delivery.

The narrator is one performer reading the whole book, so an emotion label
inferred from the words of a paragraph would make the narration lurch about.
Dialogue keeps whatever the classifier decided; narration does not.

This ran as a loop inside the pipeline runner before it was a node. It is real
analysis policy, not orchestration, and as a node it can be reordered, removed
for an experiment, or replaced with something subtler without editing the
runner.
"""

from __future__ import annotations

import logging

from ..models import AnalysisContext, Segment
from .base import Node, register

log = logging.getLogger(__name__)

NARRATION_EMOTION = "neutral"
NARRATION_INTENSITY = 0.5


def apply_narration_defaults(segments: list[Segment]) -> list[Segment]:
    for s in segments:
        if s.kind == "narration":
            s.emotion = NARRATION_EMOTION
            s.intensity = NARRATION_INTENSITY
    return segments


@register
class NarrationDefaultsNode(Node):
    """Reset narration to a neutral delivery after emotion classification."""

    name = "narration_defaults"
    requires = ("segments", "segments.kind")
    assigns = ("segments.emotion", "segments.intensity")

    async def run(self, ctx: AnalysisContext) -> None:
        ctx.segments = apply_narration_defaults(ctx.segments)
