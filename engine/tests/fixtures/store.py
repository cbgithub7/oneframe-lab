"""What the model store's tests need about files: their sha256, bytes placed in a store as a
finished download would leave them, and the rows a manifest pins them by.

`sha` is hashlib's, never the engine's own file hash: a test's expected value does not come from
the code it checks."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from oneframe.models.store import Store


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(data_root: Path, *contents: bytes) -> None:
    """Place files in the store as a finished download would."""
    store = Store(data_root)
    store.claim()
    for data in contents:
        partial = store.partial(sha(data))
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(data)
        store.place(partial, sha(data))


def url_file(name: str, data: bytes, *, format: str, url: str | None = None, **more: Any) -> dict[str, Any]:
    """A manifest's row for a file on a plain URL, by default one on files.example named for it."""
    url = url or f"https://files.example/{name}.pt"
    return {"name": name, "url": url, "size": len(data), "sha256": sha(data), "format": format, **more}


def hub_file(
    name: str, repo: str, commit: str, path: str, data: bytes, *, format: str, **more: Any
) -> dict[str, Any]:
    """A manifest's row for a file in a hub repository at a commit."""
    row = {"name": name, "hf": repo, "revision": commit, "path": path, "size": len(data), "sha256": sha(data)}
    return {**row, "format": format, **more}
