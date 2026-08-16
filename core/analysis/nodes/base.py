"""The contract every analysis node satisfies, and the registry of them.

A node is one step of the analysis. It reads the shared ``AnalysisContext``,
does one job, and writes its result back. Every node has the same shape, so the
pipeline can hold them in a list and a new one can be dropped anywhere in that
list without touching the runner or its neighbours.

Nodes declare what they ``requires`` and ``assigns``. The declaration is what
makes reordering safe to attempt: the pipeline can be checked before it runs
and say "emotion_classifier needs segments, nothing before it produces them"
instead of failing halfway through a chapter. This mirrors how spaCy validates
component order with ``analyze_pipes``, and carries the same caveat — the check
believes what a node declares, so a wrong declaration hides a real problem.

The vocabulary is deliberately small and dotted: ``text`` and ``title`` come
from the request, ``segments``, ``characters`` and ``validation`` are context
fields, and ``segments.<attr>`` means "this attribute of the segments is
meaningfully populated" (``segments.speaker``, ``segments.emotion``).

Node classes are plain and stateless. Anything a node needs from the outside,
such as a language model, arrives on the context, so nothing here reaches for a
global or builds its own client.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import AnalysisContext

SEED_KEYS: tuple[str, ...] = ("text", "title")


class Node(ABC):
    """One step of the analysis."""

    name: str = ""
    node_type: str = "programmatic"  # "programmatic" | "ai"
    requires: tuple[str, ...] = ()
    assigns: tuple[str, ...] = ()

    @abstractmethod
    async def run(self, ctx: AnalysisContext) -> None:
        """Read what this node needs from *ctx* and write its result back."""

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.name!r}>"


_REGISTRY: dict[str, type[Node]] = {}


def register(cls: type[Node]) -> type[Node]:
    """Register a node class under its ``name``, for building by name."""
    if not cls.name:
        raise ValueError(f"{cls.__name__} must set a name")
    if cls.name in _REGISTRY and _REGISTRY[cls.name] is not cls:
        raise ValueError(f"duplicate node name {cls.name!r}")
    _REGISTRY[cls.name] = cls
    return cls


def create(name: str) -> Node:
    if name not in _REGISTRY:
        known = ", ".join(sorted(_REGISTRY)) or "none"
        raise KeyError(f"unknown node {name!r}; registered: {known}")
    return _REGISTRY[name]()


def registered() -> list[str]:
    return sorted(_REGISTRY)
