"""Writing the files the app keeps, and locking across processes.

Every file the app keeps says its format (AGENTS.md, "One data root"), and a newer one is never
overwritten: an older engine that meets a file a newer one wrote leaves it as it is, so going back
a version never costs what the newer one learned. `write_json` is the one way such a file is
written: to a temporary file of its own beside the target, flushed to disk, then put in place in
one step, so an interrupted write leaves the previous file and two writers never share a
temporary.

`FileLock` is an operating-system lock on a file under `logs/locks/`: `fcntl.flock` or
`msvcrt.locking`, never `lockf`, which lets one process take the same lock twice. The operating
system releases it when its holder dies, however it dies, so a lock is never left behind and a lock
file is never deleted (deleting one would let two processes each hold a lock on a different file of
the same name).
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import stat
import sys
import time
import uuid
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

_LOCK_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


class NewerFormat(RuntimeError):
    """The file on disk is in a newer format than this engine writes; it is left as it is."""

    def __init__(self, path: Path, found: int, known: int):
        super().__init__(
            f"{path.name} is in format {found}, newer than this version of the app knows ({known}); "
            "it was left as it is"
        )
        self.path = path
        self.found = found
        self.known = known


def format_of(data: Any, key: str = "format", missing: int | None = None) -> int | None:
    """The format a parsed file says it is in; `missing` when it does not say. A value that is not
    a whole number reads as None, which no reader takes for its own."""
    if not isinstance(data, dict):
        return None
    value = data.get(key, missing)
    if isinstance(value, bool) or not isinstance(value, int):
        return None if key in data else missing
    return value


def _on_disk_format(path: Path, key: str) -> int | None:
    try:
        return format_of(json.loads(path.read_text(encoding="utf-8")), key)
    except OSError, ValueError:
        return None  # missing or unreadable: nothing worth keeping


def write_json(path: Path, data: dict[str, Any], *, key: str = "format", indent: int | None = None) -> None:
    """Write `data` to `path` in one step, unless the file there is in a newer format.

    `data[key]` is the format being written. Raises NewerFormat, and changes nothing, when the
    file on disk says a larger one."""
    ours = format_of(data, key)
    if ours is None:
        raise ValueError(f"{path.name}: {key!r} must say the format being written")
    found = _on_disk_format(path, key)
    if found is not None and found > ours:
        raise NewerFormat(path, found, ours)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=indent, separators=None if indent else (",", ":"), ensure_ascii=False)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as handle:
            handle.write(text + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def _replace(source: Path, target: Path) -> None:
    """os.replace, waiting briefly on Windows, which refuses while another process has the target
    open to read."""
    for attempt in range(20):
        try:
            source.replace(target)
            return
        except PermissionError:
            if sys.platform != "win32" or attempt == 19:
                raise
            time.sleep(0.05)


def _clear_readonly(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    """rmtree that also removes read-only files, which Windows refuses to delete otherwise."""
    shutil.rmtree(path, onexc=_clear_readonly)


class FileLock:
    """A lock on one file, held by this object until `release`, or until the process ends. Never
    waits: `acquire` says at once whether it got the lock. A second FileLock on the same file is
    refused, in this process as in any other."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._fd: int | None = None
        self._closer: weakref.finalize[[int], FileLock] | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)  # one byte, which may lie past the end
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        # A lock dropped without release (a test's engine, say) is closed when it is collected.
        self._closer = weakref.finalize(self, os.close, fd)
        return True

    def release(self) -> None:
        fd, self._fd = self._fd, None
        if fd is None:
            return
        if self._closer is not None:
            self._closer.detach()
            self._closer = None
        try:
            if sys.platform == "win32":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)  # which releases an flock too

    def __enter__(self) -> FileLock:
        if not self.acquire():
            raise LockBusy(self.path)
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()


class LockBusy(RuntimeError):
    """Another process (or another part of this one) holds the lock."""

    def __init__(self, path: Path):
        super().__init__(f"{path.stem} is in use by another process")
        self.path = path


def lock_file(locks: Path, name: str) -> Path:
    """`<locks>/<name>.lock`, for a name that is safe as a file name on every system."""
    if not _LOCK_NAME.match(name):
        raise ValueError(f"not a lock name: {name!r}")
    return locks / f"{name}.lock"


def is_free(path: Path) -> bool:
    """Whether nobody holds the lock on `path` now. Only a hint, since another process may take it
    the moment after; anything that acts on the answer takes the lock itself."""
    probe = FileLock(path)
    if not probe.acquire():
        return False
    probe.release()
    return True
