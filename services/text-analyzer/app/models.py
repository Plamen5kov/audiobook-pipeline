"""Data models for the hybrid text-analysis pipeline."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .llm import LLMClient


ALLOWED_EMOTIONS = frozenset([
    "neutral", "happy", "sad", "angry",
    "fearful", "excited", "tense", "contemplative", "curious",
])


@dataclass
class Segment:
    """Internal mutable segment flowing through the pipeline.

    Nodes progressively enrich fields (speaker, emotion, etc.).
    Converted to the output dict format only at the end.
    """

    id: int
    kind: str  # "dialogue" | "narration"
    original_text: str
    # What should actually be said. Written by the normalisation node; empty
    # until it runs. `original_text` stays verbatim so validation can prove the
    # segments still reconstruct the source.
    spoken_text: str = ""
    speaker: str = "unknown"  # "narrator" | character name | "unknown"
    attribution_source: str = "none"  # "explicit" | "turn_taking" | "ai" | "pronoun" | "default"
    emotion: str = "neutral"
    intensity: float = 0.5
    pause_before_ms: int = 0
    paragraph_index: int = 0
    char_offset_start: int = 0
    char_offset_end: int = 0


@dataclass
class AnalysisContext:
    """The state one analysis run threads through every node.

    Nodes take the context instead of a bespoke argument list. That is the
    whole point: a node added in the middle reads what it needs and writes what
    it produces without any other node's signature changing, so inserting one
    is an edit to the pipeline's node list and nothing else.

    ``meta`` is deliberately open. A node that wants to record something no
    other node knows about puts it there rather than growing this class.
    """

    text: str
    title: str = ""
    llm: "LLMClient | None" = None
    segments: list[Segment] = field(default_factory=list)
    characters: list[dict] = field(default_factory=list)
    validation: Optional[dict] = None
    # Per-book pronunciation overrides, applied by the normalisation node.
    # Terms whose spoken form no rule can derive: acronyms, invented names,
    # slang. Someone has to have listened to decide these.
    lexicon: dict[str, str] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeMetrics:
    """Timing for one pipeline node."""

    node_name: str
    node_type: str  # "programmatic" | "ai"
    duration_ms: int = 0


@dataclass
class PipelineResult:
    """Complete output of the pipeline."""

    title: str
    characters: list[dict] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
