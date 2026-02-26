"""Node 6 — Validation.

Verifies that the concatenation of all segment ``original_text`` values
reproduces the original input (modulo whitespace and stripped quotes).
Logs warnings for issues but does NOT fail the request.
"""

from __future__ import annotations

import logging
import re
from ..models import Segment

log = logging.getLogger(__name__)


def validate_completeness(
    segments: list[Segment], original_text: str
) -> tuple[bool, list[str]]:
    """Check that every word of *original_text* appears in exactly one segment.

    Returns ``(passed, issues)`` where *issues* is a list of human-readable
    problem descriptions (empty when *passed* is True).
    """
    issues: list[str] = []

    if not segments:
        issues.append("No segments produced")
        return False, issues

    # --- Strategy: compare normalised word sequences ---
    original_norm = _normalise(original_text)
    reconstructed = " ".join(s.original_text for s in sorted(segments, key=lambda s: s.id))
    reconstructed_norm = _normalise(reconstructed)

    if original_norm == reconstructed_norm:
        return True, issues

    # Locate the first divergence point for debugging.
    orig_words = original_norm.split()
    recon_words = reconstructed_norm.split()

    # Check for missing words.
    orig_set = set(orig_words)
    recon_set = set(recon_words)

    missing = orig_set - recon_set
    if missing:
        sample = list(missing)[:10]
        issues.append(f"Words in original but not in segments: {sample}")

    extra = recon_set - orig_set
    if extra:
        sample = list(extra)[:10]
        issues.append(f"Words in segments but not in original: {sample}")

    # Find first positional mismatch.
    for j in range(min(len(orig_words), len(recon_words))):
        if orig_words[j] != recon_words[j]:
            context_orig = " ".join(orig_words[max(0, j - 3):j + 4])
            context_recon = " ".join(recon_words[max(0, j - 3):j + 4])
            issues.append(
                f"First word mismatch at position {j}: "
                f"original=...{context_orig}... "
                f"reconstructed=...{context_recon}..."
            )
            break

    if len(orig_words) != len(recon_words):
        issues.append(
            f"Word count mismatch: original={len(orig_words)}, "
            f"reconstructed={len(recon_words)}"
        )

    for issue in issues:
        log.warning("Validation: %s", issue)

    return len(issues) == 0, issues


def _normalise(text: str) -> str:
    """Strip quotes, collapse whitespace, lowercase for comparison."""
    text = text.replace("\u201c", "").replace("\u201d", "")
    text = text.replace('"', "")
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text
