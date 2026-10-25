"""A job and its result: the only form a node run is asked for in and answered with.

A `Job` is what the scheduler or a bench asks one node run to do; `Done` is what the run gave. Both
cross into a runtime child as JSON, written and read here and nowhere else: the job as the file the
child is started with, the result as its `done` event. One engine writes and reads them within one
run, so neither carries a format number.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe.node import Value
from oneframe.node.usage import PEAKS


@dataclass(frozen=True)
class Job:
    """One node run. `entry` is `file.py:function`, a file in `folder`, the node's code."""

    node: str
    folder: Path
    entry: str
    out_dir: Path
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
        return json.dumps(
            {
                "node": self.node,
                "folder": str(self.folder),
                "entry": self.entry,
                "out_dir": str(self.out_dir),
                "run": self.run,
                "step": self.step,
                "inputs": {port: value.to_json() for port, value in self.inputs.items()},
                "params": self.params,
                "device": self.device,
                "files": self.files,
                "attempt": self.attempt,
                "precision": self.precision,
                "memory_budget_mb": self.memory_budget_mb,
                "vram_cap_mb": self.vram_cap_mb,
                "outside_torch_mb": self.outside_torch_mb,
            },
            default=str,
        )

    @classmethod
    def from_json(cls, text: str) -> Job:
        row = json.loads(text)
        return cls(
            node=row["node"],
            folder=Path(row["folder"]),
            entry=row["entry"],
            out_dir=Path(row["out_dir"]),
            run=row["run"],
            step=row["step"],
            inputs={port: Value.from_json(value) for port, value in row["inputs"].items()},
            params=row["params"],
            device=row["device"],
            files=row["files"],
            attempt=row["attempt"],
            precision=row["precision"],
            memory_budget_mb=row["memory_budget_mb"],
            vram_cap_mb=row["vram_cap_mb"],
            outside_torch_mb=row["outside_torch_mb"],
        )


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
        return cls(
            outputs=event["outputs"],
            seconds=event["seconds"],
            peaks={key: event.get(key) for key in PEAKS},
            ways=event["ways"],
            stats=event["stats"],
        )
