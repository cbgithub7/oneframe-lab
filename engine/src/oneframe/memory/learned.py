"""What each machine learns about each node, kept under the data root: the store a fit reads
and a run records into."""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oneframe.files import NewerFormat, format_of, write_json
from oneframe.layout import Layout
from oneframe.memory.estimate import Learned

# The memory package's one logger, as before it was a package: logs and diagnostics read the same.
LOGGER = logging.getLogger("oneframe.memory")

STORE_VERSION = 1
STORE_ENTRIES = 256  # the least recently used entry goes first
RECENT_RATIOS = 8
RECENT_SETTINGS = 16
MIN_WORKING_MB = 64  # a smaller working estimate gives a ratio that is noise
LOWER_AFTER_RUNS = 2  # successful runs that must agree before a correction goes below 1


@dataclass(frozen=True)
class StoreKey:
    """What a node's learning is kept under: a new version, memory model or runtime lock starts it
    over, and each kind of device learns on its own."""

    node: str
    version: str
    model: str  # the memory model's hash; "" without one
    lock: str  # the runtime lock's hash; "" for none

    def text(self, kind: str) -> str:
        return "|".join((self.node, self.version, self.model, self.lock, kind))


class LearnedStore:
    """`<data>/memory/learned.json`: per node and kind of device, the recent working-memory ratios,
    and the measured need and seconds for recent settings. Its `version` is its format. Written
    with `files.write_json`, so an interrupted write leaves the previous file. A file in a newer
    format is left as it is and not used: this machine learns in memory until the newer app runs
    again. Without a data root it lives in memory only."""

    def __init__(self, data_root: Path | None) -> None:
        self.path = None if data_root is None else Layout(Path(data_root)).learned
        self._data: dict[str, Any] | None = None
        self.newer: int | None = None  # the format of a newer file left alone
        # A run records while nodes.fit and nodes.forget read and clear, from another thread.
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = {"version": STORE_VERSION, "clock": 0, "entries": {}}
            if self.path is not None:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    data = None
                found = format_of(data, "version")
                if found == STORE_VERSION and isinstance(data, dict):
                    self._data = data
                elif found is not None and found > STORE_VERSION:
                    self.newer = found
                    LOGGER.warning("%s is in format %s, newer than this app's; left alone", self.path, found)
        return self._data

    def _save(self) -> None:
        if self.path is None or self._data is None or self.newer is not None:
            return
        try:
            write_json(self.path, self._data, key="version")
        except NewerFormat as exc:  # a newer app wrote it since this one read it
            self.newer = exc.found
            LOGGER.warning("%s", exc)

    def view(self, key: StoreKey) -> Learned:
        with self._lock:
            return self._view(key)

    def record(self, key: StoreKey, kind: str, **measured: Any) -> None:
        """One run's measurement; see `_record` for what it takes."""
        with self._lock:
            self._record(key, kind, **measured)

    def forget(self, node: str) -> int:
        with self._lock:
            return self._forget(node)

    def _view(self, key: StoreKey) -> Learned:
        """What a fit reads: a correction per kind of device, and needs and seconds per settings."""
        prefix = key.text("")
        corrections: dict[str, float] = {}
        peaks: dict[tuple[str, str], float] = {}
        seconds: dict[tuple[str, str], float] = {}
        for text, entry in self._load()["entries"].items():
            if not text.startswith(prefix):
                continue
            kind = text[len(prefix) :]
            correction = _correction(entry.get("ratios") or [])
            if correction is not None:
                corrections[kind] = correction
            for settings, mb in (entry.get("peaks") or {}).items():
                peaks[(kind, settings)] = float(mb)
            for settings, value in (entry.get("seconds") or {}).items():
                seconds[(kind, settings)] = float(value)
        return Learned(corrections, peaks, seconds)

    def _record(
        self,
        key: StoreKey,
        kind: str,
        *,
        settings: str,
        ok: bool,
        need_mb: float | None,
        working_mb: float | None,
        weights_mb: float | None,
        outside_mb: float = 0.0,
        seconds: float | None = None,
    ) -> None:
        """One run's measurement. `need_mb` is what it used on the device, in the fit's terms (on a
        card, torch's peak reserved memory plus the memory outside torch; on the processor, the
        growth of its resident memory); `working_mb` is the working estimate before any
        correction, and `weights_mb` the weights it held on the device. A failed run's need is a
        lower bound: it adds a ratio only above 1, and no need or seconds."""
        data = self._load()
        entries: dict[str, Any] = data["entries"]
        text = key.text(kind)
        entry = entries.pop(text, None) or {"ratios": [], "peaks": {}, "seconds": {}}
        if (
            need_mb is not None
            and working_mb is not None
            and weights_mb is not None
            and working_mb >= MIN_WORKING_MB
        ):
            ratio = max(need_mb - weights_mb - outside_mb, 0.0) / working_mb
            if ok or ratio > 1:
                entry["ratios"] = [*entry["ratios"], {"ratio": round(ratio, 4), "ok": ok}][-RECENT_RATIOS:]
        if ok and need_mb is not None:
            entry["peaks"] = _recent(entry["peaks"], settings, round(need_mb, 1))
        if ok and seconds is not None:
            entry["seconds"] = _recent(entry["seconds"], settings, round(seconds, 3))
        data["clock"] = int(data.get("clock", 0)) + 1
        entry["used"] = data["clock"]
        entries[text] = entry
        while len(entries) > STORE_ENTRIES:
            oldest = min(entries, key=lambda k: entries[k].get("used", 0))
            del entries[oldest]
        self._save()

    def _forget(self, node: str) -> int:
        """Drops everything learned about a node, every version and kind of device; how many
        entries went."""
        entries: dict[str, Any] = self._load()["entries"]
        gone = [text for text in entries if text.split("|", 1)[0] == node]
        for text in gone:
            del entries[text]
        if gone:
            self._save()
        return len(gone)


def _recent(rows: dict[str, Any], settings: str, value: float) -> dict[str, Any]:
    """The newest value for these settings, keeping the last few settings."""
    rows = {k: v for k, v in rows.items() if k != settings}
    rows[settings] = value
    return dict(list(rows.items())[-RECENT_SETTINGS:])


def _correction(ratios: list[dict[str, Any]]) -> float | None:
    """The largest recent ratio. It goes below 1 only when at least two successful runs agree;
    until then a smaller ratio leaves the estimate as it is."""
    if not ratios:
        return None
    largest = max(float(r["ratio"]) for r in ratios)
    if largest < 1 and sum(1 for r in ratios if r.get("ok")) < LOWER_AFTER_RUNS:
        return None
    return largest
