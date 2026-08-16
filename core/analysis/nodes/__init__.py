"""Analysis nodes.

Importing this package imports every node module in it, which is what puts the
nodes in the registry. Discovery is why adding a node is a matter of dropping a
file in this directory: nothing else has a list of modules to keep in step, so
no existing file has to be edited to make a new node exist.

Modules whose name starts with an underscore are skipped, and so is ``base``,
which defines the registry rather than filling it.
"""

from __future__ import annotations

import importlib
import pkgutil

_SKIP = {"base"}


def _discover() -> list[str]:
    found = []
    for info in pkgutil.iter_modules(__path__):
        if info.name.startswith("_") or info.name in _SKIP:
            continue
        importlib.import_module(f"{__name__}.{info.name}")
        found.append(info.name)
    return found


discovered = _discover()
