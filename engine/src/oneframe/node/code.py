"""A node's code, imported for a run as a package named for the node.

A node's folder is the package `oneframe_nodes.<id as a name>_<8 hex of the folder's normalised
path>`, its entry a module in it, so a node imports its own helpers relatively (`from . import
utils`) and `import utils` still reaches the runtime's package of that name. `sys.path` never
changes: no helper hides a runtime's package, and two nodes' helpers never meet. Before a run the
package's modules are forgotten by name, so a node in the engine's process runs its code as it is
now, as its cache key (the hash of its folder) says it does.

The path is hashed here with `hashlib`: node code cannot import the engine's own hashing.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

from oneframe.node import NodeFailed

ROOT = "oneframe_nodes"


def package_name(node: str, folder: Path) -> str:
    """The package a node's folder is imported as: one folder is one package, on Windows too."""
    where = os.path.normcase(os.path.normpath(str(folder.resolve())))
    digest = hashlib.sha256(where.encode("utf-8")).hexdigest()[:8]
    name = "".join(c if c.isalnum() else "_" for c in node)
    return f"{ROOT}.{name}_{digest}"


def load_code(
    node: str, folder: Path, entry: str, load: str | None = None
) -> tuple[Callable[..., Any], Callable[..., Any] | None]:
    """The functions `entry` and `load` (each `file.py:function`) name, in a fresh import of the
    node's folder; None for a node without `load`."""
    package = _package(package_name(node, folder), folder)
    return _function(package, entry), None if load is None else _function(package, load)


def _package(name: str, folder: Path) -> ModuleType:
    for known in [m for m in sys.modules if m == name or m.startswith(name + ".")]:
        del sys.modules[known]
    root = sys.modules.get(ROOT)
    if root is None:
        root = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(ROOT, None, is_package=True))
        sys.modules[ROOT] = root
    init = folder / "__init__.py"
    if init.is_file():
        spec = importlib.util.spec_from_file_location(name, init, submodule_search_locations=[str(folder)])
    else:
        spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
        spec.submodule_search_locations = [str(folder)]
    if spec is None:
        raise ImportError(f"Cannot load node code from {folder}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    if spec.loader is not None:
        spec.loader.exec_module(module)
    setattr(root, name.rpartition(".")[2], module)
    return module


def _function(package: ModuleType, entry: str) -> Callable[..., Any]:
    file, _, function = entry.partition(":")
    module = importlib.import_module(".".join([package.__name__, *Path(file).with_suffix("").parts]))
    fn = getattr(module, function, None)
    if not callable(fn):
        raise NodeFailed(f"{Path(file).name} has no function {function}().")
    return fn
