"""The language-model dependency, as an interface rather than a URL.

Nodes that need a model depend on ``LLMClient``, not on Ollama, on httpx, or on
a base URL. That is what lets a node be tested against a scripted fake with no
server running, and lets the backend change without touching a node.

This module deliberately imports nothing. The concrete client lives beside the
HTTP library it needs, in ``app.ollama_client``, so importing the analysis
does not pull in a transport. ``LLMClient`` is a Protocol rather than a base
class for the same reason: an implementation only has to have the method, so a
test double is a plain object that never imports this module at all.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(self, system_prompt: str, user_prompt: str) -> dict:
        """Return the model's reply parsed as JSON."""
        ...
