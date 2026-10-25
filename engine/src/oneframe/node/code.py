"""A node's code, imported for a run."""

from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe.node import NodeFailed


def load_entry(file: str | Path, function: str) -> Callable[..., Any]:
    """Import a node's code by path under a private module name, so two nodes whose files are both
    called node.py never collide, and the node's own folder is importable for its helpers. Modules
    an earlier run imported from that folder are forgotten first, so a node in the engine's process
    runs its helpers as they are now, as its cache key (the hash of its folder) says it does."""
    path = Path(file).resolve()
    folder = str(path.parent)
    inside = os.path.normcase(folder) + os.sep
    for name, module in list(sys.modules.items()):
        where = getattr(module, "__file__", None)
        if where and os.path.normcase(str(Path(where).resolve())).startswith(inside):
            del sys.modules[name]
    if folder not in sys.path:
        sys.path.insert(0, folder)
    name = "oneframe_node_" + "".join(c if c.isalnum() else "_" for c in str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load node code from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, function, None)
    if not callable(fn):
        raise NodeFailed(f"{path.name} has no function {function}().")
    return fn
