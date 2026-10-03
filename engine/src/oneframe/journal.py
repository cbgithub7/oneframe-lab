"""Failures leave evidence: every event of a run or an install, appended to a journal.

`Scheduler.run` and `Runtimes.run_install` wrap the `emit` they are given, so a run or an install
started by the app or by a command-line tool leaves the same file: `logs/journal/<when>-<what>.ndjson`,
one JSON object per line, each with the time it was written, in the order they were emitted. A
journal stops growing at JOURNAL_MAX_BYTES, saying so in its last line.

Journals and the per-step logs (`logs/steps/`) are pruned when a journal opens: the newest are
kept, up to a count and a total size set here, so evidence never fills the disk.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger(__name__)

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
    """One run's or install's journal. Thread-safe: a node's events and an install's log lines
    arrive from reader threads."""

    def __init__(self, folder: Path, what: str):
        now = datetime.now(UTC)
        folder.mkdir(parents=True, exist_ok=True)
        prune(folder, JOURNALS_KEEP - 1, JOURNALS_MAX_BYTES, "*.ndjson")
        name = f"{now.strftime('%Y%m%dT%H%M%S%fZ')}-{_SAFE.sub('-', what).strip('-') or 'journal'}.ndjson"
        self.path = folder / name
        self._lock = threading.Lock()
        self._written = 0
        self._full = False

    def write(self, event: dict[str, Any]) -> None:
        row = {"at": datetime.now(UTC).isoformat(timespec="milliseconds"), **event}
        line = json.dumps(row, default=str, ensure_ascii=False) + "\n"
        with self._lock:
            if self._full:
                return
            if self._written + len(line) > JOURNAL_MAX_BYTES:
                self._full = True
                line = json.dumps({"at": row["at"], "event": "journal.full", "bytes": self._written}) + "\n"
            try:
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(line)
                self._written += len(line)
            except OSError as exc:  # evidence is never worth failing the run for
                LOGGER.warning("could not write the journal %s: %s", self.path, exc)

    def wrap(self, emit: Emit) -> Emit:
        def journalled(event: dict[str, Any]) -> None:
            self.write(event)
            emit(event)

        return journalled


def journalled(emit: Emit, folder: Path | None, what: str) -> Emit:
    """`emit`, also writing each event to a new journal in `folder`; `emit` itself without one."""
    if folder is None:
        return emit
    return Journal(folder, what).wrap(emit)


def prune_step_logs(folder: Path | None) -> None:
    if folder is not None:
        prune(folder, STEP_LOGS_KEEP, STEP_LOGS_MAX_BYTES, "*.log")
