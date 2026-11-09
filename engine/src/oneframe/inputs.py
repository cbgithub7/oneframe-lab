"""Files a graph reads, kept by content: a photo the person picked, copied into the root's `inputs/`.

An import is named by its sha256, `inputs/<sha256><suffix>`, and that hash is its id: the page
gets the id and the file's name, never its path, and a `file` param holds the id. So a saved graph
holds no path, opens after a restart, and names only what was imported. The same photo imported
from two places is one file. Nothing else is kept about it.

The copy is made to a temporary file beside the target and hashed there, so the id is the hash of
the bytes kept, even if the original changes while it is read.
"""

from __future__ import annotations

import contextlib
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from oneframe.files import replace, sha256_file, temporary_for
from oneframe.layout import Layout
from oneframe.reading import is_sha256

if TYPE_CHECKING:
    from oneframe.graph import Graph
    from oneframe.registry import Registry

_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")


def find(folder: Path, ident: str) -> Path | None:
    """The file of the import `ident` (a sha256) in `folder`, the layout's `inputs`, or None."""
    if not is_sha256(ident) or not folder.is_dir():
        return None
    for path in sorted(folder.glob(f"{ident}*")):
        if path.is_file() and (path.name == ident or path.name.startswith(f"{ident}.")):
            return path
    return None


def import_file(layout: Layout, source: Path) -> tuple[str, str]:
    """Copy `source` into `inputs/`, once per content: its id (the sha256) and its file's name."""
    if not source.is_file():
        raise FileNotFoundError(f"No file at {source}.")
    folder = layout.inputs
    folder.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower() if _SUFFIX.match(source.suffix.lower()) else ""
    temporary = temporary_for(folder / f"import{suffix}")
    try:
        shutil.copyfile(source, temporary)
        ident = sha256_file(temporary)
        if find(folder, ident) is None:
            replace(temporary, folder / f"{ident}{suffix}")
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)
    return ident, source.name


def import_paths(graph: Graph, registry: Registry, layout: Layout) -> None:
    """Each `file` param of `graph` that names a path imported, and named by its id instead, as the
    page's dialog does: how a command-line tool reads a graph that names its photo by path. A
    relative path is the working folder's. Raises FileNotFoundError for a path with no file."""
    for gnode in graph.nodes.values():
        manifest = registry.nodes.get(gnode.node)
        for name, param in manifest.params.items() if manifest is not None else ():
            value = gnode.params.get(name)
            if param.type == "file" and isinstance(value, str) and not is_sha256(value):
                gnode.params[name] = import_file(layout, Path(value).resolve())[0]
