"""The node API: what a node's code may import from the engine, and nothing else of it.

Node code runs in the engine's process or in a runtime child, an interpreter where nothing of the
engine is installed; the child makes this package, alone, importable there (child.py). So it is
standard library only, its modules import nothing of the engine outside it, and it is checked for
Python 3.11, the oldest a runtime may run (ruff.toml, pyrightconfig.json).

    Value       one value on a port
    Stopped     the run was stopped
    NodeFailed  a failure the node words for the person (ctx.fail)
    Emit        where an event goes

What runs a job lives beside it: the context a node is handed (context.py), its code (code.py),
what a run uses (usage.py), what a failure is called (failures.py), a child's guards (process.py)
and the runner (runner.py).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

__all__ = ["Emit", "NodeFailed", "Stopped", "Value"]

Emit = Callable[[dict[str, Any]], None]


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
