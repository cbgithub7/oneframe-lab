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

from oneframe.files import replace, sha256_file, temporary_for
from oneframe.layout import Layout

ID = re.compile(r"^[0-9a-f]{64}$")
_SUFFIX = re.compile(r"^\.[a-z0-9]{1,10}$")


def is_id(value: object) -> bool:
    """Whether `value` has the form of an import's id: a sha256, in lower-case hex."""
    return isinstance(value, str) and bool(ID.match(value))


def find(folder: Path, ident: str) -> Path | None:
    """The file of the import `ident` in `folder` (the layout's `inputs`), or None."""
    if not is_id(ident) or not folder.is_dir():
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
