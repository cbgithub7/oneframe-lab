"""Failures leave evidence: every event of a run or an install, appended to a journal.

`Scheduler.run` and `Runtimes.run_install` wrap the `emit` they are given, so a run or an install
started by the app or by a command-line tool leaves the same file: `logs/journal/<when>-<what>.ndjson`,
one JSON object per line, each with the time it was written, in the order they were emitted, after
a first line that says the journal's format. A journal stops growing at JOURNAL_MAX_BYTES, saying so
in its last line.

Journals and the per-step logs (`logs/steps/`) are pruned when a journal opens: the newest are
kept, up to a count and a total size set here, so evidence never fills the disk.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

LOGGER = logging.getLogger(__name__)

JOURNAL_FORMAT = 1  # the first line: {"event": "journal", "format": 1, "what": ...}
JOURNALS_KEEP = 100
JOURNALS_MAX_BYTES = 64 * 2**20
JOURNAL_MAX_BYTES = 16 * 2**20  # one journal; uv can be talkative
STEP_LOGS_KEEP = 200
STEP_LOGS_MAX_BYTES = 128 * 2**20

Emit = Callable[[dict[str, Any]], None]
_SAFE = re.compile(r"[^A-Za-z0-9._-]+")


def prune(folder: Path, keep: int, max_bytes: int, pattern: str = "*") -> list[Path]:
    """Delete the oldest files in `folder` (by modification time) until at most `keep` remain and
    together they hold at most `max_bytes`. The newest file is always kept. Returns what went."""
    try:
        files = [p for p in folder.glob(pattern) if p.is_file()]
    except OSError:
        return []
    rows = []
    for path in files:
        with contextlib.suppress(OSError):
            stat = path.stat()
            rows.append((stat.st_mtime_ns, path.name, path, stat.st_size))
    rows.sort(reverse=True)  # newest first
    gone: list[Path] = []
    total = 0
    for index, (_mtime, _name, path, size) in enumerate(rows):
        total += size
        if index == 0 or (index < keep and total <= max_bytes):
            continue
        try:
            path.unlink()
            gone.append(path)
        except OSError as exc:  # held open on Windows; the next prune tries again
            LOGGER.warning("could not prune %s: %s", path, exc)
    return gone


class Journal:
    """One run's or install's journal, open until `close`. Its first line says its format. Thread
    safe: a node's events and an install's log lines arrive from reader threads. A journal that
    cannot be written is skipped, with a warning: evidence is never worth failing the run for."""

    def __init__(self, folder: Path, what: str):
        now = datetime.now(UTC)
        name = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{_SAFE.sub('-', what).strip('-') or 'journal'}.ndjson"
        self.path = folder / name
        self._lock = threading.Lock()
        self._written = 0
        self._full = False
        self._handle: TextIO | None = None
        try:
            folder.mkdir(parents=True, exist_ok=True)
            prune(folder, JOURNALS_KEEP - 1, JOURNALS_MAX_BYTES, "*.ndjson")
            self._handle = self.path.open("a", encoding="utf-8")
        except OSError as exc:
            LOGGER.warning("could not start the journal %s: %s", self.path, exc)
            return
        self.write({"event": "journal", "format": JOURNAL_FORMAT, "what": what})

    def write(self, event: dict[str, Any]) -> None:
        row = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), **event}
        line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
        with self._lock:
            if self._full or self._handle is None:
                return
            if self._written + len(line) > JOURNAL_MAX_BYTES:
                self._full = True
                line = json.dumps({"at": row["at"], "event": "journal.full", "bytes": self._written}) + "\n"
            try:
                self._handle.write(line)
                self._handle.flush()  # a crash a moment later still leaves this line
                self._written += len(line)
            except OSError as exc:
                LOGGER.warning("could not write the journal %s: %s", self.path, exc)

    def close(self) -> None:
        with self._lock:
            handle, self._handle = self._handle, None
        if handle is not None:
            with contextlib.suppress(OSError):
                handle.close()

    def wrap(self, emit: Emit) -> Emit:
        def journalled(event: dict[str, Any]) -> None:
            self.write(event)
            emit(event)

        return journalled


@contextlib.contextmanager
def journalled(emit: Emit, folder: Path | None, what: str) -> Iterator[Emit]:
    """`emit`, also writing each event to a new journal in `folder` until the block ends; `emit`
    itself without a folder."""
    if folder is None:
        yield emit
        return
    journal = Journal(folder, what)
    try:
        yield journal.wrap(emit)
    finally:
        journal.close()


def prune_step_logs(folder: Path | None) -> None:
    if folder is not None:
        prune(folder, STEP_LOGS_KEEP, STEP_LOGS_MAX_BYTES, "*.log")
