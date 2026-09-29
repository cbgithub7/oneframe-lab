"""Every node the engine can run, found by looking in folders, never by a list in code.

A broken manifest does not hide the others: it is kept as a problem the app can show next to the
node catalogue, so a person adding a node sees what is wrong with it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from oneframe import BUILTIN_NODES_DIR
from oneframe.manifest import Manifest, ManifestError, load


@dataclass
class Registry:
    nodes: dict[str, Manifest] = field(default_factory=dict)
    problems: dict[str, list[str]] = field(default_factory=dict)

    def get(self, node_id: str) -> Manifest:
        try:
            return self.nodes[node_id]
        except KeyError:
            raise KeyError(f"No node called {node_id!r} is installed.") from None

    def converters(self) -> list[Manifest]:
        """Nodes the graph may insert on its own between two ports that differ only in facets: a
        convert node with exactly one input and one output, and no parameter without a default."""
        out = []
        for m in self.nodes.values():
            if m.category != "convert" or len(m.inputs) != 1 or len(m.outputs) != 1:
                continue
            if any(p.default is None for p in m.params.values()):
                continue
            out.append(m)
        return sorted(out, key=lambda m: m.id)


def discover(roots: Iterable[Path] | None = None) -> Registry:
    registry = Registry()
    for root in roots if roots is not None else [BUILTIN_NODES_DIR]:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*/node.json")):
            try:
                manifest = load(path)
            except ManifestError as exc:
                registry.problems[str(path)] = exc.problems
                continue
            if manifest.id in registry.nodes:
                other = registry.nodes[manifest.id].folder
                registry.problems[str(path)] = [f"id {manifest.id!r} is already used by {other}"]
                continue
            registry.nodes[manifest.id] = manifest
    return registry
