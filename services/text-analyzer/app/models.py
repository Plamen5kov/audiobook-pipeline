"""Data models for the hybrid text-analysis pipeline."""

from dataclasses import dataclass, field
from typing import Optional


ALLOWED_EMOTIONS = frozenset([
    "neutral", "happy", "sad", "angry",
    "fearful", "excited", "tense", "contemplative",
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
    speaker: str = "unknown"  # "narrator" | character name | "unknown"
    attribution_source: str = "none"  # "explicit" | "turn_taking" | "ai" | "pronoun" | "default"
    emotion: str = "neutral"
    intensity: float = 0.5
    pause_before_ms: int = 0
    paragraph_index: int = 0
    char_offset_start: int = 0
    char_offset_end: int = 0


@dataclass
class NodeMetrics:
    """Timing and stats for one pipeline node."""

    node_name: str
    node_type: str  # "programmatic" | "ai"
    duration_ms: int = 0
    segments_processed: int = 0
    segments_affected: int = 0  # how many segments this node changed


@dataclass
class PipelineResult:
    """Complete output of the pipeline."""

    title: str
    characters: list[dict] = field(default_factory=list)
    segments: list[dict] = field(default_factory=list)
    report: dict = field(default_factory=dict)
