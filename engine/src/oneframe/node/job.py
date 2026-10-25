"""A job and its result: the only form a node run is asked for in and answered with.

A `Job` is what the scheduler or a bench asks one node run to do; `Done` is what the run gave. Both
cross into a runtime child as JSON, written and read here and nowhere else: the job as the file the
child is started with, the result as its `done` event. One engine writes and reads them within one
run, so neither carries a format number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from oneframe.node import Value
from oneframe.node.usage import PEAKS


@dataclass(frozen=True)
class Job:
    """One node run. `entry` is `file.py:function`, a file in `folder`, the node's code; `load`,
    when the node has one, is the function called once before it, in the same form."""

    node: str
    folder: Path
    entry: str
    out_dir: Path
    load: str | None = None
    run: str = ""
    step: str = ""
    inputs: dict[str, Value] = field(default_factory=dict)
    params: dict[str, Any] = field(default_factory=dict)
    device: str = "cpu"
    files: dict[str, str] = field(default_factory=dict)  # by name in the manifest: the store's path
    attempt: int = 1  # 2 on the engine's retry after running out
    precision: str | None = None
    memory_budget_mb: float | None = None  # the fit's budget on the device; None when not fitted
    vram_cap_mb: float | None = None  # the budget the child caps the card at; None: free memory only
    outside_torch_mb: float = 0.0  # what the node allocates on the card outside torch

    def to_json(self) -> str:
        """The job as the child reads it. A param that is not plain JSON is written as its `str`."""
        row = {f.name: getattr(self, f.name) for f in fields(self)}
        row["inputs"] = {port: value.to_json() for port, value in self.inputs.items()}
        return json.dumps(row, default=str)

    @classmethod
    def from_json(cls, text: str) -> Job:
        row = json.loads(text)
        row.update(
            folder=Path(row["folder"]),
            out_dir=Path(row["out_dir"]),
            inputs={port: Value.from_json(value) for port, value in row["inputs"].items()},
        )
        return cls(**row)


@dataclass(frozen=True)
class Done:
    """What a run gave: each output the node declared (`path`, `facets`, `meta`), how long it
    took, its peaks (`peak_vram_mb`, `peak_reserved_mb`, `peak_ram_mb`, each None where unknown),
    the way each ctx.fallbacks step finished with, and the stats its function returned."""

    outputs: dict[str, dict[str, Any]]
    seconds: float
    peaks: dict[str, float | None]
    ways: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_event(self) -> dict[str, Any]:
        """The child's `done` event."""
        return {
            "event": "done",
            "outputs": self.outputs,
            "seconds": self.seconds,
            **self.peaks,
            "ways": self.ways,
            "stats": self.stats,
        }

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Done:
        """A `done` event as the child wrote it. One missing a part reads as having none: an output
        it lacks is then the node's contract failure, never the engine's crash."""
        return cls(
            outputs=dict(event.get("outputs") or {}),
            seconds=float(event.get("seconds") or 0.0),
            peaks={key: event.get(key) for key in PEAKS},
            ways=dict(event.get("ways") or {}),
            stats=dict(event.get("stats") or {}),
        )
