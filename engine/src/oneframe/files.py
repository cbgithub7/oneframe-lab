"""Writing the files the app keeps, and locking across processes.

Every file the app keeps says its format (AGENTS.md, "One data root"), and a newer one is never
overwritten: an older engine that meets a file a newer one wrote leaves it as it is, so going back
a version never costs what the newer one learned. `write_json` is the one way such a file is
written: to a temporary file of its own beside the target, flushed to disk, then put in place in
one step, so an interrupted write leaves the previous file and two writers never share a
temporary.

`FileLock` is an operating-system lock on a file under `logs/locks/`: `fcntl.flock` or
`msvcrt.locking`, never `lockf`, which lets one process take the same lock twice. The operating
system releases it when its holder dies, however it dies (on Windows, a moment after it has ended),
so a lock is never left behind and a lock file is never deleted (deleting one would let two
processes each hold a lock on a different file of the same name).

`sha256_file` is the one loop that hashes a file, whatever the hash is for (a cache key, a check
of a stored file, a download resuming its partial), so no recorded hash depends on which caller
read the bytes. `replace` is the one move into place, of a file or a folder, and `free_bytes` the
one reading of a drive's free space.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
import weakref
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe.errors import Stopped

CHUNK = 1 << 20  # what one read takes of a file being hashed
# How long a move into place waits on Windows while another program (an antivirus, the indexer)
# holds the target: about what uv waits; pip's 1 s is known to be too short (spec 003's research.md).
MOVE_WAIT_S = 10.0
_LOCK_NAME = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
# What flock and msvcrt.locking say when another holder has the lock; anything else is a failure.
_BUSY = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK, errno.EDEADLK}


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
    text = json.dumps(data, indent=indent, separators=None if indent else (",", ":"), ensure_ascii=False)
    write_text(path, text + "\n")


_TEMPORARY = re.compile(r"^\..+\.[0-9a-f]{12}\.tmp$")


def temporary_for(path: Path) -> Path:
    """Where write_text puts the new file before it replaces `path`: beside it, on its volume."""
    return path.with_name(f".{path.name}.{uuid.uuid4().hex[:12]}.tmp")


def is_temporary(name: str) -> bool:
    """A file write_text was writing when its process was killed outright, which nothing may read
    (a node's code key skips it)."""
    return bool(_TEMPORARY.match(name))


def write_text(path: Path, text: str) -> None:
    """Write `text` to `path` in one step: a reader, or an error or Ctrl+C partway, sees the old file
    or the new one, never part of either. Lines end in LF on every system, so a file's bytes (and a
    manifest's digest) do not depend on where it was written. A process killed outright can leave
    the temporary behind (is_temporary)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = temporary_for(path)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        replace(temporary, path)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def replace(source: Path, target: Path, windows: bool | None = None) -> None:
    """os.replace, waiting on Windows while another program holds the target, with a growing pause
    between tries. A target that is a folder is never tried again: Windows refuses to replace one
    as it refuses a held file, and never stops refusing. Anything else, or a hold that outlasts the
    wait (MOVE_WAIT_S, read when called), raises the system's error."""
    windows = sys.platform == "win32" if windows is None else windows
    deadline = time.monotonic() + MOVE_WAIT_S
    pause = 0.05
    while True:
        try:
            source.replace(target)
            return
        except PermissionError:
            if not windows or time.monotonic() >= deadline or target.is_dir():
                raise
            time.sleep(pause)
            pause = min(pause * 2, 1.0)


def sha256_file(
    path: Path,
    stop: threading.Event | None = None,
    progress: Callable[[int], None] | None = None,
    digest: hashlib._Hash | None = None,
) -> str:
    """The sha256 of a file, read in chunks: Stopped when `stop` is set, seen between chunks, and
    `progress` told the bytes read so far after each. `digest`: a running hash to carry on (a
    download that resumes its partial), updated in place."""
    digest = digest if digest is not None else hashlib.sha256()
    done = 0
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            if stop is not None and stop.is_set():
                raise Stopped()
            digest.update(chunk)
            done += len(chunk)
            if progress is not None:
                progress(done)
    return digest.hexdigest()


def text_sha256(path: Path) -> str:
    """The sha256 of a text file with its line endings made LF, so a checkout that turned them into
    CRLF does not make an installed runtime look out of date."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def free_bytes(path: Path) -> int:
    """Free space on the drive that holds `path`, or will: that of its nearest folder that exists."""
    probe = Path(path).resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    return shutil.disk_usage(probe).free


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
        except OSError as exc:
            os.close(fd)
            if exc.errno in _BUSY:
                return False
            raise  # locking itself failed (a share without locks, say): not the same as busy
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
