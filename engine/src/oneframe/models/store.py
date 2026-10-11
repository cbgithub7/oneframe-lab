"""The store: each file once, named by its sha256, read-only, with a record of what was checked.

    models/oneframe-store.json     the store's format
    models/blobs/<2>/<sha256>      each file, read-only
    models/partial/<sha256>        a download not yet checked; url-<hash of the URL> while pinning
    models/records/<sha256>.json   the file's size and modification time when its sha256 matched

A file is present once its sha256 matched and its record holds its size and modification time;
a run compares both with the file, which costs a stat, and `models.verify` hashes again. The store
and each record say their format: an older app never rewrites a newer one, and a newer or unreadable
store refuses every change (`StoreRefused`) while runs treat its files as not present.

Only a download, verify, remove or pin changes the store, one at a time across processes (their
lock is the job's, in jobs.py), so nothing here locks. Every path is built from a checked sha256,
so a delete never leaves the store's own folders; a delete clears read-only first, and one the
system refuses keeps the file and its record (`os_refused`), never reported as an engine bug.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import re
import shutil
import stat
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oneframe.errors import Failure, Stopped
from oneframe.files import NewerFormat, format_of, write_json
from oneframe.layout import Layout

STORE_FORMAT = 1
RECORD_FORMAT = 1
CHUNK = 1 << 20
# How long placing a file waits on Windows while another program (an antivirus, the indexer) holds
# the new file: about what uv waits; pip's 1 s is known to be too short (research.md).
PLACE_WAIT_S = 10.0
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class StoreRefused(Failure):
    """The store cannot do this now; what it holds is as it was."""

    kind = "store"

    def __init__(self, message: str, reason: str, next: str | None = None):
        super().__init__(message, reason=reason, next=next)


@dataclass(frozen=True)
class Record:
    sha256: str
    size: int
    mtime_ns: int


@dataclass(frozen=True)
class Presence:
    """What the store holds of one file.

    status: `present` (checked, and its size and modification time still match); `missing`
    (nothing); `partial` (a download not yet finished); `changed` (a file whose record is missing,
    unreadable, or no longer matches it); `newer` (a newer app recorded it, or wrote the store);
    `unreadable` (the store's format file cannot be read)."""

    status: str
    have: int  # bytes on disk: the whole file when present, else what a partial holds
    why: str = ""


def check_sha256(sha: str) -> str:
    if not _SHA256.match(sha):
        raise ValueError(f"not a sha256: {sha!r}")
    return sha


def hash_file(
    path: Path, should_stop: Callable[[], bool] | None = None, progress: Callable[[int], None] | None = None
) -> str:
    """The file's sha256, read in chunks; Stopped when `should_stop` says so between chunks."""
    digest = hashlib.sha256()
    done = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            if should_stop is not None and should_stop():
                raise Stopped()
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done)
    return digest.hexdigest()


def _writable(path: Path) -> None:
    with contextlib.suppress(FileNotFoundError):
        path.chmod(stat.S_IREAD | stat.S_IWRITE)


def _delete(path: Path) -> None:
    """Delete one file, clearing read-only first (Windows refuses otherwise). Missing is fine."""
    if not path.exists():
        return
    try:
        _writable(path)
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise StoreRefused(
            f"The system refused to delete {path} ({exc}); it and its record were kept.",
            "os_refused",
            "Close any program using the file, then try again.",
        ) from exc


def _rename(source: Path, target: Path, windows: bool | None = None, wait_s: float = PLACE_WAIT_S) -> None:
    """os.replace, waiting on Windows while another program holds the new file, with a growing
    pause between tries; anything else, or a hold that outlasts the wait, is `os_refused`."""
    windows = sys.platform == "win32" if windows is None else windows
    deadline = time.monotonic() + wait_s
    pause = 0.05
    while True:
        try:
            source.replace(target)
            return
        except PermissionError as exc:
            if not windows or time.monotonic() >= deadline:
                raise StoreRefused(
                    f"The system refused to move {source.name} into the store ({exc}).",
                    "os_refused",
                    "An antivirus may be scanning it; try again in a moment.",
                ) from exc
            time.sleep(pause)
            pause = min(pause * 2, 1.0)
        except OSError as exc:
            raise StoreRefused(
                f"The system refused to move {source.name} into the store ({exc}).", "os_refused"
            ) from exc


class Store:
    def __init__(self, root: Path):
        self.layout = Layout(root)

    # where things are

    def blob(self, sha: str) -> Path:
        return self.layout.blobs / check_sha256(sha)[:2] / sha

    def record_path(self, sha: str) -> Path:
        return self.layout.records / f"{check_sha256(sha)}.json"

    def partial(self, sha: str) -> Path:
        return self.layout.partials / check_sha256(sha)

    def url_partial(self, url: str) -> Path:
        """Where a file whose sha256 is not known yet (while pinning) is downloaded."""
        return self.layout.partials / f"url-{hashlib.sha256(url.encode()).hexdigest()}"

    # the store's own format

    def state(self) -> str:
        """`absent` (nothing written yet), `ok`, `newer` or `unreadable`."""
        try:
            data = json.loads(self.layout.store_file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "absent"
        except OSError, ValueError:
            return "unreadable"
        found = format_of(data)
        if found is None:
            return "unreadable"
        return "newer" if found > STORE_FORMAT else "ok"

    def claim(self) -> None:
        """Before a download, verify, remove or pin changes anything: a newer or unreadable store is
        refused and left as it is; a store without a format file gets one."""
        found = self.state()
        where = self.layout.store_file
        if found == "newer":
            raise StoreRefused(
                f"The model store at {self.layout.models} was written by a newer version of Oneframe Lab; "
                "it was left as it is.",
                "newer_format",
                "Use the newer version of the app.",
            )
        if found == "unreadable":
            raise StoreRefused(
                f"{where} cannot be read, or does not say its format; nothing in the store was changed.",
                "unreadable",
                f"Look at {where}.",
            )
        if found == "absent":
            try:
                write_json(where, {"format": STORE_FORMAT}, indent=2)
            except NewerFormat as exc:  # written by a newer app a moment ago
                raise StoreRefused(str(exc), "newer_format", "Use the newer version of the app.") from exc

    # records

    def record(self, sha: str) -> Record | str:
        """The file's record, or why there is none: `missing`, `unreadable` or `newer`."""
        try:
            data = json.loads(self.record_path(sha).read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "missing"
        except OSError, ValueError:
            return "unreadable"
        found = format_of(data)
        if found is not None and found > RECORD_FORMAT:
            return "newer"
        size, mtime = data.get("size"), data.get("mtime_ns")
        if (
            found is None
            or data.get("sha256") != sha
            or not isinstance(size, int)
            or not isinstance(mtime, int)
        ):
            return "unreadable"
        return Record(sha, size, mtime)

    def presence(self, sha: str, size: int, state: str | None = None) -> Presence:
        """Whether a run may read this file now, without hashing it. `state`: the store's own, when
        the caller has read it once for many files."""
        state = state if state is not None else self.state()
        if state == "newer":
            return Presence("newer", 0, "the store was written by a newer version of the app")
        if state == "unreadable":
            return Presence("unreadable", 0, f"{self.layout.store_file} cannot be read")
        found = self.record(sha)
        try:
            info = self.blob(sha).stat()
        except FileNotFoundError:
            info = None
        if info is None:
            partial = self.partial(sha)
            with contextlib.suppress(FileNotFoundError):
                return Presence("partial", min(partial.stat().st_size, size))
            return Presence("missing", 0)
        if found == "newer":
            return Presence("newer", 0, "a newer version of the app recorded it")
        if not isinstance(found, Record):
            return Presence("changed", 0, f"its record is {found}")
        if info.st_size != found.size or found.size != size:
            return Presence("changed", 0, f"it holds {info.st_size} bytes, not {size}")
        if info.st_mtime_ns != found.mtime_ns:
            return Presence("changed", 0, "it was modified after it was checked")
        return Presence("present", size)

    def recorded(self, sha: str) -> Record:
        """Record a stored file whose sha256 the caller has just checked (verify)."""
        return self._write_record(sha, self.blob(sha))

    def _write_record(self, sha: str, path: Path) -> Record:
        info = path.stat()
        record = Record(sha, info.st_size, info.st_mtime_ns)
        write_json(
            self.record_path(sha),
            {
                "format": RECORD_FORMAT,
                "sha256": sha,
                "size": record.size,
                "mtime_ns": record.mtime_ns,
                "checked": datetime.now(UTC).isoformat(timespec="seconds"),
            },
            indent=2,
        )
        return record

    # changes: each one only under the job's lock

    def place(self, source: Path, sha: str) -> Record:
        """Move a file whose sha256 the caller has checked into the store, read-only, and record it.
        A record in a newer format is refused (`newer_format`), and nothing moves."""
        if self.record(sha) == "newer":
            raise StoreRefused(
                f"{sha[:12]}… was recorded by a newer version of the app; it was left as it is.",
                "newer_format",
                "Use the newer version of the app.",
            )
        target = self.blob(sha)
        target.parent.mkdir(parents=True, exist_ok=True)
        _writable(target)  # a leftover without a good record: replaced
        _rename(source, target)
        try:
            target.chmod(stat.S_IREAD)
            return self._write_record(sha, target)
        except NewerFormat as exc:
            raise StoreRefused(str(exc), "newer_format", "Use the newer version of the app.") from exc
        except OSError as exc:
            raise StoreRefused(f"The store could not record {sha[:12]}… ({exc}).", "os_refused") from exc

    def adopt(self, sha: str, should_stop: Callable[[], bool] | None = None) -> bool:
        """A stored file without a good record is hashed again: recorded if it matches, deleted
        if not. Whether it is present now. A newer record is left alone (False)."""
        found = self.record(sha)
        if found == "newer":
            return False
        blob = self.blob(sha)
        if not blob.is_file():
            if isinstance(found, Record) or found == "unreadable":
                _delete(self.record_path(sha))
            return False
        if hash_file(blob, should_stop) != sha:
            self.drop(sha)
            return False
        self._write_record(sha, blob)
        return True

    def drop(self, sha: str) -> None:
        """Delete a file, then its record, so a record never outlives its file. A delete the system
        refuses keeps both (`os_refused`). A newer record is refused (`newer_format`)."""
        if self.record(sha) == "newer":
            raise StoreRefused(
                f"{sha[:12]}… was recorded by a newer version of the app; it was left as it is.",
                "newer_format",
                "Use the newer version of the app.",
            )
        _delete(self.blob(sha))
        _delete(self.record_path(sha))

    def drop_record(self, sha: str) -> None:
        """Forget that a file was checked, leaving the file: a run may still be reading it."""
        _delete(self.record_path(sha))

    def drop_partial(self, path: Path) -> None:
        if path.parent != self.layout.partials:
            raise ValueError(f"{path} is not a partial download")
        _delete(path)

    def partials(self) -> list[Path]:
        try:
            return sorted(p for p in self.layout.partials.iterdir() if p.is_file())
        except FileNotFoundError:
            return []

    def stored(self) -> list[str]:
        """The sha256 of every file in the store."""
        try:
            return sorted(
                p.name
                for folder in self.layout.blobs.iterdir()
                if folder.is_dir()
                for p in folder.iterdir()
                if _SHA256.match(p.name) and p.name[:2] == folder.name
            )
        except FileNotFoundError:
            return []

    def free_bytes(self) -> int:
        """Free space on the store's drive (its nearest existing folder's)."""
        folder = self.layout.models
        while not folder.exists() and folder != folder.parent:
            folder = folder.parent
        return shutil.disk_usage(folder).free
