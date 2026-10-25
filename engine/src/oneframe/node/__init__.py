"""The node API: what a node's code may import from the engine, and nothing else of it.

Node code runs in the engine's process or in a runtime child, an interpreter where nothing of the
engine is installed; the child makes this package, alone, importable there (child.py). So it is
standard library only, its modules import nothing of the engine outside it, and it is checked for
Python 3.11, the oldest a runtime may run (ruff.toml, pyrightconfig.json).

    Context      what `run` is handed: `def run(ctx: Context)`
    LoadContext  what `load` is handed, without the run's inputs, paths or outputs
    Value        one value on a port
    Stopped      the run was stopped
    NodeFailed   a failure the node words for the person (ctx.fail)
    Emit         where an event goes

The two contexts hold exactly what docs/nodes.md documents. `Stopped` and `NodeFailed` are here for
a node's `except` to let through; a node fails through `ctx.fail`.

What runs a job lives beside it: the context a node is handed (context.py), its code (code.py),
what a run uses (usage.py), what a failure is called (failures.py), a child's guards (process.py)
and the runner (runner.py).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn, Protocol, TypeVar

__all__ = ["Context", "Emit", "LoadContext", "NodeFailed", "Stopped", "Value"]

Emit = Callable[[dict[str, Any]], None]
T = TypeVar("T")


class NodeFailed(RuntimeError):
    """A node reports a failure it understands, in words meant for the person running it, through
    `ctx.fail(message)`."""


class Stopped(RuntimeError):
    """The run was stopped."""


class Value:
    """One value on a port: its type, its facets (each with one value), a file, and notes.

    `path` is a file (or, for a ViewSet, a folder). `meta` is small JSON the node wants to pass on
    (image size, a focal length); anything large goes in the file."""

    __slots__ = ("facets", "key", "meta", "path", "trust", "type")

    def __init__(
        self,
        type: str,
        path: str | Path,
        facets: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
        trust: str | None = None,
        key: str = "",
    ):
        self.type = type
        self.path = Path(path)
        self.facets = dict(facets or {})
        self.meta = dict(meta or {})
        self.trust = trust
        self.key = key

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": str(self.path),
            "facets": self.facets,
            "meta": self.meta,
            "trust": self.trust,
            "key": self.key,
        }

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> Value:
        return cls(
            row["type"], row["path"], row.get("facets"), row.get("meta"), row.get("trust"), row.get("key", "")
        )

    def __repr__(self) -> str:
        return f"Value({self.type}, {self.path.name}, {self.facets})"


class LoadContext(Protocol):
    """What a node's `load` is handed: where it runs and within what memory, the files it reads,
    and how it reports. Nothing of one run's inputs, paths or outputs, so what `load` returns can
    be kept for later runs."""

    params: dict[str, Any]  # the fitted values of the node's params
    device: str  # "cpu" or "cuda"
    precision: str  # "fp32" unless the memory model picked another
    attempt: int  # 1, or 2 on the engine's one retry after running out of memory
    memory_budget_mb: float | None  # the fit's budget on this device; None if not fitted

    def memory_free_mb(self) -> float | None:
        """What is left under the cap (on the processor, of the budget), in MB; None if unknown."""
        ...

    def fallbacks(self, step: str, ways: Sequence[tuple[str, Callable[[], T]]]) -> T:
        """One step, each way in turn while torch runs out of memory (docs/nodes.md)."""
        ...

    def file(self, name: str) -> Path:
        """A file the manifest names, as the model store holds it. Read-only."""
        ...

    def stage(self, name: str, message: str = "") -> None: ...

    def progress(self, done: float, total: float | None = None, what: str = "") -> None: ...

    def check_stop(self) -> None:
        """Raises Stopped once Stop was pressed (in the engine's process; a runtime's just ends)."""
        ...

    def fail(self, message: str) -> NoReturn:
        """Ends the run with kind `node` and this message, as it is written."""
        ...


class Context(LoadContext, Protocol):
    """What a node's `run` is handed: its `load` context, and this run's inputs and outputs."""

    inputs: dict[str, Value]  # by input port; an optional port left unconnected is missing

    def path(self, name: str) -> Path:
        """A path for a file this run writes, inside its own folder."""
        ...

    def output(
        self,
        port: str,
        path: str | Path,
        facets: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        """Declares an output; every one the manifest lists, before `run` returns."""
        ...
