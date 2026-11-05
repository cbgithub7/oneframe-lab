"""Spec 005's check of the model nodes with their real weights (AC2), which CI's `models (cpu)` job
runs once every runtime and every node's files are in:

    node scripts/tool.js engine/tests/models/run_on_cpu.py [--data <root>] [--nodes <folder>]...
    uv run --project engine --frozen python engine/tests/models/run_on_cpu.py --cache-key

Every node that runs in a runtime is fed the 512 px photo beside this file, through the node that
brings a photo in (found by its ports, not its name), in a graph of the two. Each run must end
`done`, the scheduler having checked every output against its port, on whatever device the fit
picks (the processor, on a machine with no card); every valid depth it gives must lie above 0 and
below 1,000 m. A node that needs more than an image, or whose runtime or files are missing, is a
failure, named. Each run is bench:fit's: a cache of its own, so nothing is served, and nothing is
learned. Nothing here names a node or a runtime (spec 013).

`--cache-key` prints the key CI keeps the model store under: a hash of the sha256s of every file
the nodes pin, so the cache changes only when the files do.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__
from oneframe.bench_fit import make_scheduler
from oneframe.graph import Graph
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.manifest import Manifest
from oneframe.node import Value
from oneframe.registry import Registry, discover
from oneframe.runtimes.manager import Runtimes, find_uv
from oneframe.scratch import for_tool

PHOTO = Path(__file__).resolve().parent / "photo-512.jpg"
DEPTH_M = (0.0, 1000.0)  # every valid depth lies strictly between these (spec 005, decisions)


def cache_key(registry: Registry) -> str:
    pinned = sorted({f.sha256 for m in registry.nodes.values() for f in m.files})
    return "models-" + hashlib.sha256("\n".join(pinned).encode("utf-8")).hexdigest()[:16]


def photo_source(registry: Registry) -> Manifest:
    """The node that brings a photo in: no inputs, one Image out, and a file to read it from."""
    found = [
        m
        for m in registry.nodes.values()
        if not m.inputs
        and [spec.type for spec in m.outputs.values()] == ["Image"]
        and any(p.type == "file" for p in m.params.values())
    ]
    if len(found) != 1:
        raise SystemExit(f"Expected one node that brings a photo in; found {len(found)}.")
    return found[0]


def graph_for(source: Manifest, node: Manifest) -> Graph | str:
    """The photo wired to each of the node's Image inputs; or why a photo alone cannot feed it."""
    param = next(name for name, p in source.params.items() if p.type == "file")
    edges = []
    for port, spec in node.inputs.items():
        if spec.type == "Image":
            edges.append({"from": f"photo.{next(iter(source.outputs))}", "to": f"step.{port}"})
        elif not spec.optional:
            return f"it needs a {spec.type} on {port}, which a photo alone cannot give"
    if not edges:
        return "it takes no image"
    nodes = {"photo": {"node": source.id, "params": {param: str(PHOTO)}}, "step": {"node": node.id}}
    return Graph.from_json({"version": 1, "nodes": nodes, "edges": edges})


def depth_check(value: Value) -> tuple[str | None, str]:
    """Why a Depth output fails the check (None when it passes), and what it holds, as text."""
    import numpy as np

    with np.load(value.path) as data:
        depth = data["depth"]
        valid = data["valid"].astype(bool) if "valid" in data else np.isfinite(depth)
    if valid.shape != depth.shape:
        return f"its valid mask is {valid.shape}, its depth {depth.shape}", ""
    shown = depth[valid]
    if shown.size == 0:
        return "no pixel has a valid depth", ""
    low, high = float(shown.min()), float(shown.max())
    held = f"{low:.2f} to {high:.2f} m, {valid.mean():.0%} valid"
    if not (np.isfinite(shown).all() and DEPTH_M[0] < low and high < DEPTH_M[1]):
        return (
            f"its valid depths run from {low:g} to {high:g} m, not within {DEPTH_M[0]:g} to {DEPTH_M[1]:g}",
            held,
        )
    return None, held


def check(data: Path, registry: Registry, manager: Runtimes) -> int:
    source = photo_source(registry)
    nodes = sorted((m for m in registry.nodes.values() if m.run.where == "runtime"), key=lambda m: m.id)
    if not nodes:
        print("No node runs in a runtime: nothing to check.")
        return 1
    failed = 0
    with for_tool(Layout(data)) as mine:
        for node in nodes:
            graph = graph_for(source, node)
            if isinstance(graph, str):
                print(f"FAILED {node.id}: {graph}.")
                failed += 1
                continue
            events: list[dict[str, Any]] = []
            scheduler = make_scheduler(
                data, registry, manager, mine.make("cpu-") / "cache", tmp_root=mine.folder
            )
            print(f"{node.id}: running on the {PHOTO.name} photo", flush=True)
            result = scheduler.run(graph, events.append)
            done = next(
                (e for e in events if e.get("event") == "node.done" and e.get("step") == "step"), None
            )
            if result.status != "done" or done is None:
                error = result.error or {}
                print(f"FAILED {node.id}: {result.status}, {error.get('kind')}: {error.get('message')}")
                failed += 1
                continue
            checked = {
                p: depth_check(v) for p, v in result.outputs.get("step", {}).items() if v.type == "Depth"
            }
            problems = [f"{port}: {why}" for port, (why, _held) in checked.items() if why]
            where = f"on {done.get('made_with', {}).get('device')} in {done.get('seconds')} s"
            if problems:
                print(f"FAILED {node.id}, {where}: {'; '.join(problems)}")
                failed += 1
                continue
            depths = "; ".join(f"{port} {held}" for port, (_why, held) in checked.items())
            print(f"ok {node.id}, {where}: {depths or 'no depth output'}")
    print(f"{len(nodes) - failed} of {len(nodes)} model nodes passed.")
    return 1 if failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_on_cpu", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
    parser.add_argument(
        "--nodes", type=Path, action="append", default=None, help="node folders (default: nodes/)"
    )
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="runtime definitions")
    parser.add_argument("--cache-key", action="store_true", help="print the model store's cache key and end")
    args = parser.parse_args(argv)
    registry = discover(args.nodes or [BUILTIN_NODES_DIR])
    if registry.problems:
        for path, problems in registry.problems.items():
            print(f"{path}: {'; '.join(problems)}", file=sys.stderr)
        return 1
    if args.cache_key:
        print(cache_key(registry))
        return 0
    try:
        data, warnings = open_root(args.data.resolve() if args.data else None, version=__version__)
    except RootRefused as exc:
        print(exc, file=sys.stderr)
        return 1
    for warning in warnings:
        print(warning, file=sys.stderr)
    return check(data.resolve(), registry, Runtimes(data.resolve(), args.runtimes, uv=find_uv(None)))


if __name__ == "__main__":
    raise SystemExit(main())
