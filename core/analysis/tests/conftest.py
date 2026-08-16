"""Shared fixtures for the analysis tests."""

import sys
from pathlib import Path

# The repo root, so `core.analysis...` imports resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
