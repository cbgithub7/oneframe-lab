"""A node's code, imported for a run as a package named for the node.

A node's folder is the package `oneframe_nodes.<id as a name>_<8 hex of the folder's normalised
path>`, its entry a module in it, so a node imports its own helpers relatively (`from . import
utils`) and `import utils` still reaches the runtime's package of that name. `sys.path` never
changes: no helper hides a runtime's package, and two nodes' helpers never meet. Before a run the
package's modules are forgotten by name, so a node in the engine's process runs its code as it is
now, as its cache key (the hash of its folder) says it does.

The entry is loaded by its path, so any file the manifest accepts loads, whatever its name; a
folder's `__init__.py`, if it has one, is not run. The path is hashed here with `hashlib`: node
code cannot import the engine's own hashing.
"""

from __future__ import annotations

import hashlib
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
    digest = hashlib.sha256(os.fsencode(where)).hexdigest()[:8]
    return f"{ROOT}.{_name(node)}_{digest}"


def load_code(
    node: str, folder: Path, entry: str, load: str | None = None
) -> tuple[Callable[..., Any], Callable[..., Any] | None]:
    """The functions `entry` and `load` (each `file.py:function`) name, in a fresh import of the
    node's folder; None for a node without `load`."""
    package = _package(package_name(node, folder), folder)
    run = _function(package, folder, entry)
    return run, None if load is None else _function(package, folder, load)


def _name(text: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in text)


def _package(name: str, folder: Path) -> ModuleType:
    for known in [m for m in list(sys.modules) if m == name or m.startswith(name + ".")]:
        del sys.modules[known]
    root = sys.modules.get(ROOT)
    if root is None:
        root = importlib.util.module_from_spec(importlib.machinery.ModuleSpec(ROOT, None, is_package=True))
        sys.modules[ROOT] = root
    spec = importlib.machinery.ModuleSpec(name, None, is_package=True)
    spec.submodule_search_locations = [str(folder)]
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    setattr(root, name.rpartition(".")[2], module)
    return module


def _function(package: ModuleType, folder: Path, entry: str) -> Callable[..., Any]:
    file, _, function = entry.partition(":")
    name = ".".join([package.__name__, *(_name(part) for part in Path(file).with_suffix("").parts)])
    module = sys.modules.get(name)  # `load` and `run` in one file, or a helper that imported it
    if module is None:
        spec = importlib.util.spec_from_file_location(name, folder / file)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load node code from {folder / file}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            del sys.modules[name]
            raise
    fn = getattr(module, function, None)
    if not callable(fn):
        raise NodeFailed(f"{Path(file).name} has no function {function}().")
    return fn
