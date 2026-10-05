"""Each process's own folder for temporary files, under the data root's `cache/tmp/`.

An engine, a bench tool, or anything else that runs nodes or installs runtimes on a root takes a
numbered folder, `cache/tmp/<n>/`, and holds the lock `logs/locks/tmp-<n>.lock` for as long as it
lives. Its run folders, its job folders and its bench scratch go inside, and so does the process's
own temporary folder when it asks (`use_for_process`), which uv and every other child inherit.

At start, a process removes only the folders whose lock is free: those of processes that have
ended, however they ended. Another process's work is never touched. Numbers are reused, so the
lock files, which are never deleted, stay as few as the processes that ever ran at once.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import tempfile
from collections.abc import Iterator
from pathlib import Path

from oneframe.files import FileLock, lock_file, remove_tree
from oneframe.layout import Layout

LOGGER = logging.getLogger(__name__)
_SLOT = re.compile(r"^[0-9]+$")
TEMP_VARIABLES = ("TMP", "TEMP", "TMPDIR")
MAX_PROCESSES = 256  # more than ever run at once on one root; a bound, so a fault cannot loop forever


def _lock(layout: Layout, slot: str) -> FileLock:
    return FileLock(lock_file(layout.locks, f"tmp-{slot}"))


def _remove(entry: Path) -> bool:
    try:
        if entry.is_dir() and not entry.is_symlink():
            remove_tree(entry)
        else:
            entry.unlink()
    except OSError as exc:  # a file another program still holds open; the next sweep tries again
        LOGGER.warning("could not remove %s: %s", entry, exc)
        return False
    return True


def sweep(layout: Layout) -> list[Path]:
    """Remove every folder in `cache/tmp/` whose lock is free, and anything there from before
    folders were locked. Returns what went."""
    removed: list[Path] = []
    if not layout.tmp.is_dir():
        return removed
    for entry in sorted(layout.tmp.iterdir()):
        if _SLOT.match(entry.name):
            lock = _lock(layout, entry.name)
            if not lock.acquire():
                continue  # a live process's folder
            try:
                if _remove(entry):
                    removed.append(entry)
            finally:
                lock.release()
        elif _remove(entry):  # a run folder from before spec 006: no process holds it
            removed.append(entry)
    return removed


class Scratch:
    """This process's folder under `cache/tmp/`, and the lock that says it is in use."""

    def __init__(self, folder: Path, lock: FileLock):
        self.folder = folder
        self._lock = lock
        self._before: tuple[str | None, dict[str, str | None]] | None = None

    @classmethod
    def claim(cls, layout: Layout) -> Scratch:
        """Sweep, then take the first free number whose folder is empty or can be emptied. A dead
        process's folder that something still holds open (Windows refuses to delete it) is passed
        over, and the next sweep tries it again."""
        sweep(layout)
        for n in range(MAX_PROCESSES):
            lock = _lock(layout, str(n))
            if not lock.acquire():
                continue
            folder = layout.tmp / str(n)
            if folder.exists() and not _remove(folder):
                lock.release()
                continue
            folder.mkdir(parents=True)
            return cls(folder, lock)
        raise RuntimeError(
            f"{MAX_PROCESSES} processes already hold a folder in {layout.tmp}, or their folders cannot be "
            "removed; close some of them, or remove the folders by hand."
        )

    @property
    def held(self) -> bool:
        return self._lock.held

    def make(self, prefix: str) -> Path:
        """A new, empty folder of its own inside this one."""
        return Path(tempfile.mkdtemp(prefix=prefix, dir=self.folder))

    def use_for_process(self) -> None:
        """Make this folder the process's temporary folder: Python's, and every child's that
        inherits the environment (uv, and the Pythons it starts), until `release`."""
        if self._before is None:
            self._before = (tempfile.tempdir, {name: os.environ.get(name) for name in TEMP_VARIABLES})
        tempfile.tempdir = str(self.folder)
        for name in TEMP_VARIABLES:
            os.environ[name] = str(self.folder)

    def release(self) -> None:
        """Give the process its temporary folder back, remove this one and let the number go. A
        process that ends without this leaves the folder to the next sweep."""
        if self._before is not None:
            tempfile.tempdir, variables = self._before
            self._before = None
            for name, value in variables.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        if not self._lock.held:
            return
        with contextlib.suppress(OSError):
            remove_tree(self.folder)
        self._lock.release()


@contextlib.contextmanager
def for_tool(layout: Layout) -> Iterator[Scratch]:
    """A command-line tool's folder, made its temporary folder too, for as long as the block runs."""
    scratch = Scratch.claim(layout)
    scratch.use_for_process()
    try:
        yield scratch
    finally:
        scratch.release()
