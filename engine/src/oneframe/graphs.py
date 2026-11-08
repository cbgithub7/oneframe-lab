"""Graphs kept as files: found by looking, opened, saved and removed by name.

A graph is `<name>.json` in a folder of graphs, its name the file's stem. The folders the engine
is given (the repo's `graphs/` by default) hold the graphs that ship; the data root's `graphs/`
holds the person's. Every `*.json` directly in a folder is one, and a broken one is listed as a
problem that hides no other. A saved graph of a shipped one's name is the one listed and opened,
and removing it brings the shipped one back; a shipped graph is never changed.

A saved graph is written by `files.write_json` under its recipe version, so a newer one is never
overwritten (kind `graph`, reason `newer_format`). It is the person's own work, removed only when
they ask for it by name (AGENTS.md, deletes).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from oneframe import reading
from oneframe.errors import Failure
from oneframe.files import NewerFormat, write_json
from oneframe.graph import Graph, GraphError

SHIPPED, SAVED = "shipped", "saved"


@dataclass(frozen=True)
class GraphFile:
    name: str
    source: str  # shipped or saved
    path: Path
    graph: Graph
    shadows: bool = False  # a saved graph over a shipped one of its name

    def summary(self) -> dict[str, Any]:
        row: dict[str, Any] = {"name": self.name, "source": self.source, "title": self.graph.title}
        if self.shadows:
            row["shadows"] = SHIPPED
        return row


@dataclass(frozen=True)
class Listing:
    graphs: dict[str, GraphFile]
    problems: dict[str, list[str]]  # by file

    def to_json(self) -> dict[str, Any]:
        return {
            "graphs": [self.graphs[name].summary() for name in sorted(self.graphs)],
            "problems": self.problems,
        }

    def get(self, name: str) -> GraphFile:
        found = self.graphs.get(name)
        if found is None:
            raise Failure(f"No graph called {name!r}.", "bad_params", kind="request")
        return found


def name_problems(name: Any) -> list[str]:
    """Why `name` cannot name a graph's file, or nothing: it is an id (`reading.check_id`), and not
    a name Windows keeps for a device."""
    if not isinstance(name, str) or not name:
        return ["a graph's name should be non-empty text"]
    problems: list[str] = []
    reading.check_id(name, problems)
    if reading.device_name(name):
        problems.append(f"{name!r} is a name Windows keeps for a device")
    return problems


def _checked_name(name: Any) -> str:
    problems = name_problems(name)
    if problems:
        raise Failure("; ".join(problems), "bad_params", kind="request")
    return str(name)


def _read(path: Path) -> Graph:
    data, _ = reading.read_object(path, reading.ProblemsError)
    try:
        return Graph.from_json(data)
    except GraphError as exc:
        where = lambda p: f"{p['node']}: " if p.get("node") else ""  # noqa: E731
        raise reading.ProblemsError(str(path), [where(p) + p["message"] for p in exc.problems]) from exc


def discover(shipped: Iterable[Path], saved: Path) -> Listing:
    """Every graph in the shipped folders, then the saved one, by name; a saved graph shadows a
    shipped one of its name, and a name used twice in one kind of folder is a problem."""
    found: dict[str, GraphFile] = {}
    problems: dict[str, list[str]] = {}
    folders = [*((folder, SHIPPED) for folder in shipped), (saved, SAVED)]
    for folder, source in folders:
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.json")):
            if not path.is_file():
                continue
            wrong = name_problems(path.stem)
            graph: Graph | None = None
            if not wrong:
                try:
                    graph = _read(path)
                except reading.ProblemsError as exc:
                    wrong = exc.problems
            before = found.get(path.stem)
            if not wrong and before is not None and before.source == source:
                wrong = [f"the name {path.stem!r} is already used by {before.path}"]
            if wrong or graph is None:
                problems[str(path)] = wrong
                continue
            found[path.stem] = GraphFile(path.stem, source, path, graph, shadows=before is not None)
    return Listing(found, problems)


def save(saved: Path, name: Any, data: Any) -> Path:
    """Write the person's graph `name`, in canonical form, refusing a newer one already there."""
    path = saved / f"{_checked_name(name)}.json"
    graph = Graph.from_json(data)
    try:
        write_json(path, graph.to_json(), key="version", indent=2)
    except NewerFormat as exc:
        raise Failure(f"The graph {name!r}: {exc}.", "newer_format", kind="graph") from exc
    return path


def remove(saved: Path, name: Any) -> Path:
    """Delete the person's graph `name`, which they asked for by name; never a shipped one."""
    path = saved / f"{_checked_name(name)}.json"
    if not path.is_file():
        raise Failure(
            f"No saved graph called {name!r}; a shipped graph is never removed.", "bad_params", kind="request"
        )
    path.unlink()
    return path
