"""What a run asks of the store (spec 003, requirement 5): files that are present, or a failure
that says what is missing and how to get it.

Before a node's process starts, after the fit and the runtime check, every file the run reads at its
fitted values (the shared files, and its checkpoint's) must be present: checked once, and its size
and modification time unchanged since. Otherwise the node fails with kind `files`, naming each file,
the bytes left to fetch and a checkpoint that is downloaded, if there is one, and no process starts.

While a run's node reads files, its process lists them in its own folder under `cache/tmp/`
(`reading.json`), so a remove or verify in any process can tell which files a run is using.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from oneframe.errors import Failure
from oneframe.files import is_free, lock_file, write_json
from oneframe.layout import Layout
from oneframe.manifest import Manifest
from oneframe.memory import Checkpoints
from oneframe.models.store import Presence, Store

READING = "reading.json"
READING_FORMAT = 1
# The reason a node gives when several of its files are missing for different reasons: the one a
# person must act on first.
ORDER = ("changed", "downloading", "incomplete", "not_downloaded")


class FilesMissing(Failure):
    """A run's files are not all present: kind `files`, with the files, the bytes left to fetch and
    a checkpoint that is downloaded."""

    kind = "files"

    def __init__(
        self,
        message: str,
        reason: str,
        next: str | None,
        files: list[dict[str, Any]],
        bytes_left: int,
        present: str | None,
    ):
        super().__init__(message, reason=reason, next=next)
        self.files = files
        self.bytes_left = bytes_left
        self.present = present

    def to_json(self, **extra: Any) -> dict[str, Any]:
        row = {"files": self.files, "bytes_left": self.bytes_left}
        if self.present is not None:
            row["present_checkpoint"] = self.present
        return super().to_json(**row, **extra)


def state_of(found: Presence, downloading: bool) -> str:
    """A file's state, as a run's failure and models.list both name it: present, not_downloaded,
    downloading, incomplete, changed, newer (a newer app wrote the store or its record) or
    unreadable (the store's format file)."""
    if found.status == "missing":
        return "not_downloaded"
    if found.status == "partial":
        return "downloading" if downloading else "incomplete"
    return found.status  # present, changed, newer, unreadable


def _reason(state: str) -> str:
    """The `files` reason for a state: a file a newer app recorded, or in a store this one cannot
    read, is not one this run can trust, as a changed one is not."""
    return "changed" if state in ("newer", "unreadable") else state


def _mb(size: int) -> str:
    return f"{size / 1e6:.1f} MB" if size < 10**9 else f"{size / 1e9:.2f} GB"


def checkpoints_for(store: Store | None, manifest: Manifest) -> Checkpoints | None:
    """Which of a node's checkpoints have every file present, and what each other one would take to
    download; None for a node without checkpoints."""
    if manifest.files_by is None:
        return None
    present: set[str] = set()
    to_fetch: dict[str, int] = {}
    state = store.state() if store is not None else None
    for choice in manifest.params[manifest.files_by].choices:
        left = 0
        for spec in manifest.files_for(choice):
            found = (
                store.presence(spec.sha256, spec.size, state) if store is not None else Presence("missing", 0)
            )
            if found.status != "present":
                left += spec.size - (found.have if found.status == "partial" else 0)
        if left == 0:
            present.add(choice)
        else:
            to_fetch[choice] = left
    return Checkpoints(manifest.files_by, frozenset(present), to_fetch)


def check_files(store: Store | None, manifest: Manifest, values: Mapping[str, Any]) -> dict[str, str]:
    """Each file a run at `values` reads, by name, at its path in the store; FilesMissing unless
    every one is present. Costs a stat per file."""
    if not manifest.files:
        return {}
    checkpoint = str(values[manifest.files_by]) if manifest.files_by else None
    wanted = manifest.files_for(checkpoint)
    paths: dict[str, str] = {}
    missing: list[tuple[Any, Presence]] = []
    state = store.state() if store is not None else None
    for spec in wanted:
        found = store.presence(spec.sha256, spec.size, state) if store is not None else Presence("missing", 0)
        if found.status == "present" and store is not None:
            paths[spec.name] = str(store.blob(spec.sha256))
        else:
            missing.append((spec, found))
    if not missing:
        return paths
    downloading = store is not None and not is_free(lock_file(store.layout.locks, "models"))
    rows: list[dict[str, Any]] = []
    for spec, found in missing:
        have = found.have if found.status == "partial" else 0
        rows.append(
            {
                "name": spec.name,
                "sha256": spec.sha256,
                "size": spec.size,
                "have": have,
                "state": state_of(found, downloading),
                **({"why": found.why} if found.why else {}),
            }
        )
    reason = next(r for r in ORDER if any(_reason(row["state"]) == r for row in rows))
    left = sum(row["size"] - row["have"] for row in rows)
    present = None
    if manifest.files_by is not None:
        found_checkpoints = checkpoints_for(store, manifest)
        others = sorted((found_checkpoints.present if found_checkpoints else set()) - {checkpoint})
        present = others[0] if others else None
    described = "; ".join(
        f"{row['name']} ({row['state'].replace('_', ' ')}, {_mb(row['size'] - row['have'])} to fetch"
        + (f": {row['why']}" if row.get("why") else "")
        + ")"
        for row in rows
    )
    at = f" at {manifest.files_by} {checkpoint}" if checkpoint is not None else ""
    message = f"{manifest.id}{at} reads files that are not ready: {described}."
    if present is not None:
        message += f" Its {manifest.files_by} {present} is downloaded."
    command = f"npm run models:download -- {manifest.id}"
    if checkpoint is not None and checkpoint != manifest.default_checkpoint:
        command += f" --checkpoint {checkpoint}"
    nexts = {
        "downloading": "Wait for the download to finish, then run again.",
        "changed": f"Download again ({command}); it checks the file and fetches it again if it must.",
    }
    if state in ("newer", "unreadable"):
        nexts["changed"] = "Use the newer version of the app, or another data root."
    raise FilesMissing(message, reason, nexts.get(reason, command), rows, left, present)


@contextlib.contextmanager
def reading(folder: Path | None, shas: list[str]) -> Iterator[None]:
    """List the files a run is reading in this process's own folder for as long as the block runs."""
    if folder is None or not shas:
        yield
        return
    path = folder / READING
    write_json(path, {"format": READING_FORMAT, "sha256": sorted(set(shas))})
    try:
        yield
    finally:
        with contextlib.suppress(OSError):
            path.unlink()


def in_use(layout: Layout) -> set[str]:
    """The sha256 of every file a run in any live process on this root is reading: the lists in
    `cache/tmp/<n>/` whose process still holds its folder's lock, this process's own included."""
    found: set[str] = set()
    try:
        folders = [p for p in layout.tmp.iterdir() if p.is_dir() and p.name.isdigit()]
    except FileNotFoundError:
        return found
    for folder in folders:
        listed = folder / READING
        if not listed.is_file() or is_free(lock_file(layout.locks, f"tmp-{folder.name}")):
            continue  # nothing read, or a process that ended: its list says nothing now
        try:
            data = json.loads(listed.read_text(encoding="utf-8"))
        except OSError, ValueError:
            continue  # being written or removed this moment
        shas = data.get("sha256") if isinstance(data, dict) else None
        if isinstance(shas, list):
            found.update(str(s) for s in shas)
    return found
