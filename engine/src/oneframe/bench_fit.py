"""The fit report: run a node at the settings given and show, in numbers, how its memory model held.

    npm run bench:fit -- <node> [--set k=v ...]... [--out <file or folder>]
    npm run bench:fit -- --graph <file> <step> [--set k=v ...]...

It runs the node once per `--set` group (once at its defaults without one) and records, for each:
the estimate, the peak it measured, their ratio, the system memory's estimate and peak, the
seconds, and the free memory before the run. With `--graph`, the node is a step of that graph, fed
its real inputs: what it depends on runs once, and the step itself runs at every group.
Spec 005 measures real models with it; spec 002's check on a card, which builds on it, lives with
its node in the tests (`npm run bench:fit-check`).

Nothing is learned between runs, so every estimate is the node's own, uncorrected. It writes a
Markdown report of every number. Nothing here imports a model library: the node runs in its
runtime's own interpreter.
"""

from __future__ import annotations

import argparse
import contextlib
import re
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__, hardware
from oneframe.cache import Cache
from oneframe.graph import Graph, GraphError, GraphNode
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.manifest import Manifest, Param, check_param
from oneframe.memory.estimate import Target, evaluate
from oneframe.memory.learned import LearnedStore, StoreKey
from oneframe.memory.settings import Settings
from oneframe.node import Value
from oneframe.reading import ProblemsError, read_object
from oneframe.registry import Registry, discover
from oneframe.runtimes.manager import RuntimeMissing, Runtimes, RuntimeSide, find_uv
from oneframe.scheduler import Scheduler, for_root
from oneframe.scratch import for_tool

TOLERANCE_SHARE = 0.10  # an estimate holds within 10% of the peak,
TOLERANCE_MB = 64  # or 64 MB, whichever is larger


# -- one run at given settings -------------------------------------------------------------------


def parse_sets(pairs: list[str], manifest: Manifest) -> dict[str, Any]:
    """`k=v` strings as the node's typed params; every problem named at once."""
    values: dict[str, Any] = {}
    problems: list[str] = []
    for pair in pairs:
        name, eq, text = pair.partition("=")
        param = manifest.params.get(name)
        if not eq or param is None:
            problems.append(f"{pair!r}: {manifest.id} has no parameter {name!r}")
            continue
        try:
            value = _typed(param, text)
        except ValueError:
            problems.append(f"{pair!r}: not a {param.type}")
            continue
        why = check_param(name, param, value)
        if why:
            problems.append(why)
        values[name] = value
    if problems:
        raise ValueError("; ".join(problems))
    return values


def _typed(param: Param, text: str) -> Any:
    if param.type == "int":
        return int(text)
    if param.type == "float":
        return float(text)
    if param.type == "bool":
        if text.lower() not in ("true", "false", "1", "0"):
            raise ValueError(text)
        return text.lower() in ("true", "1")
    return text


def holds(estimate: float | None, measured: float | None) -> bool | None:
    """Whether an estimate is within the tolerance of the measured peak; None if either is unknown."""
    if estimate is None or measured is None:
        return None
    return abs(measured - estimate) <= max(TOLERANCE_SHARE * estimate, TOLERANCE_MB)


def measure(
    scheduler: Scheduler,
    node: str,
    params: dict[str, Any],
    label: str = "",
    graph: Graph | None = None,
    step: str = "n",
) -> dict[str, Any]:
    """Run `node` once at `params` and return what the report shows of it: alone, from no cache, or
    as the step `step` of `graph`, whose other steps the scheduler's cache may serve."""
    events: list[dict[str, Any]] = []
    if graph is None:
        graph = Graph.from_json(
            {"version": 1, "nodes": {step: {"node": node, "params": params}}, "edges": []}
        )
    started = time.monotonic()
    result = scheduler.run(graph, events.append)
    seconds = round(time.monotonic() - started, 2)
    mine = [e for e in events if e.get("step") == step]
    fits = [e for e in mine if e["event"] == "node.fit"]
    done = next((e for e in mine if e["event"] == "node.done"), None)
    failed = next((e for e in mine if e["event"] == "node.failed"), None)
    last = fits[-1] if fits else {}  # the fit of the attempt that finished, or failed last
    device = (done or {}).get("made_with", {}).get("device") or last.get("device")
    needs = last.get("needs") or {}
    estimate = needs.get("device")
    measured = None
    if done is not None:
        if device == "cuda":
            reserved = done.get("peak_reserved_mb")
            outside = 0.0
            if reserved is not None:
                model = scheduler.registry.get(node).memory
                if model is not None:
                    values = fits[-1].get("values") or params
                    outside = evaluate(model.outside_torch, model, values, {})[0] or 0.0
                measured = round(float(reserved) + outside, 1)
        else:
            measured = done.get("peak_ram_mb")
    free = ((last.get("devices") or {}).get(device or "") or {}).get("free")
    system_estimate = needs.get("system")
    system_measured = (done or {}).get("peak_ram_mb")  # a card's run reports it too
    return {
        "label": label or ", ".join(f"{k}={v}" for k, v in params.items()) or "defaults",
        "params": params,
        "status": result.status,
        "error": result.error,
        "device": device,
        "estimate_mb": estimate,
        "measured_mb": measured,
        "ratio": round(measured / estimate, 3) if measured and estimate else None,
        "holds": holds(estimate, measured),
        "system_estimate_mb": system_estimate,
        "system_measured_mb": system_measured,
        "system_ratio": round(system_measured / system_estimate, 3)
        if system_measured and system_estimate
        else None,
        "served": [e["step"] for e in events if e["event"] == "node.cached"],
        "seconds": (done or {}).get("seconds", seconds),
        "free_before_mb": free,
        "fits": fits,
        "attempts": len(fits),
        "steps_oom": [e for e in events if e["event"] == "node.step_oom"],
        "ooms": [e for e in events if e["event"] == "node.oom"],
        "ceilings": [e for e in events if e["event"] == "ceiling"],
        "made_with": (done or {}).get("made_with"),
        "failed": failed,
    }


class _Unlearned(LearnedStore):
    """A store that learns nothing. `LearnedStore(None)` still learns, in memory, so one run's
    peak (an overrun's, say) would correct the estimates of every run after it."""

    def __init__(self) -> None:
        super().__init__(None)

    def record(self, key: StoreKey, kind: str, **measured: Any) -> None:
        return None


def make_scheduler(
    data: Path | None,
    registry: Registry,
    runtimes: RuntimeSide | None,
    cache_root: Path,
    settings: Callable[[], Settings] = Settings,
    tmp_root: Path | None = None,
) -> Scheduler:
    """A scheduler for the bench, built as the engine's is (scheduler.for_root), so a node reads its
    files from the root's store: but with a cache of its own (a repeated setting must run, not be
    served), nothing learned (estimates stay uncorrected), and the person's settings left alone."""

    def machine(cards: bool) -> dict[str, Any]:
        return hardware.profile(data, cards=cards)

    return for_root(
        data,
        registry,
        runtimes,
        Cache(cache_root / "objects", cache_root / "runs"),
        machine=machine,
        settings=settings,
        learned=_Unlearned(),
        tmp_root=tmp_root,
    )


def bench_node(scheduler: Scheduler, node: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Any node at the settings given: one run per group."""
    rows = []
    for i, params in enumerate(groups or [{}]):
        with fresh_cache(scheduler, i):
            rows.append(measure(scheduler, node, params))
    return {"mode": "node", "node": node, "runs": rows}


class BenchGraphError(ProblemsError):
    """A graph the bench was given that it cannot read or measure in."""


class _Serving(Cache):
    """An empty cache of its own that also serves the entries it is given from another: the steps a
    measured node depends on, run once. Anything else, the measured node above all, runs."""

    def __init__(self, objects: Path, work: Path, served: frozenset[str], other: Cache):
        super().__init__(objects, work)
        self.served = served
        self.other = other

    def get(self, key: str) -> dict[str, Value] | None:
        return self.other.get(key) if key in self.served else super().get(key)


def upstream_of(graph: Graph, key: str) -> set[str]:
    """The steps `key` depends on, through the graph's edges."""
    found: set[str] = set()
    todo = [key]
    while todo:
        here = todo.pop()
        for edge in graph.edges:
            if edge.dst == here and edge.src not in found:
                found.add(edge.src)
                todo.append(edge.src)
    return found


def part_of(graph: Graph, keys: set[str]) -> Graph:
    """The graph's steps in `keys`, and the edges between them."""
    return Graph(
        nodes={k: n for k, n in graph.nodes.items() if k in keys},
        edges=[e for e in graph.edges if e.src in keys and e.dst in keys],
        title=graph.title,
    )


def size_of(value: Value) -> str:
    """How big a value is, for the report: an image's pixels, or else its file's bytes."""
    width, height = value.meta.get("width"), value.meta.get("height")
    if isinstance(width, int) and isinstance(height, int):
        return f"{width} x {height} px"
    try:
        return f"{value.path.stat().st_size:,} bytes"
    except OSError:
        return "unknown"


def bench_in_graph(
    scheduler: Scheduler, graph: Graph, key: str, groups: list[dict[str, Any]], name: str = ""
) -> dict[str, Any]:
    """The step `key` of `graph` at the settings given, over the graph's own: what it depends on runs
    once, and the step itself, at every group, never from the cache."""
    if key not in graph.nodes:
        raise BenchGraphError(name or "the graph", [f"no step called {key!r}"])
    before = upstream_of(graph, key)
    node = graph.nodes[key].node
    served: set[str] = set()
    inputs: dict[str, str] = {}
    if before:
        first = scheduler.run(part_of(graph, before), lambda event: None)
        if first.status != "done":
            message = (first.error or {}).get("message") or first.status
            raise BenchGraphError(name or "the graph", [f"what {key!r} depends on did not run: {message}"])
        served = {str(record["key"]) for record in first.records.values() if record.get("key")}
        inputs = {
            edge.dst_port: size_of(first.outputs[edge.src][edge.src_port])
            for edge in graph.edges
            if edge.dst == key
        }
    rows = []
    for i, params in enumerate(groups or [{}]):
        measured = part_of(graph, before | {key})
        own = graph.nodes[key]
        measured.nodes[key] = GraphNode(own.node, {**own.params, **params}, own.label)
        label = ", ".join(f"{k}={v}" for k, v in params.items()) or "the graph's settings"
        with fresh_cache(scheduler, i, frozenset(served)):
            rows.append(measure(scheduler, node, params, label, graph=measured, step=key))
    return {"mode": "graph", "node": node, "graph": name, "step": key, "inputs": inputs, "runs": rows}


@contextlib.contextmanager
def fresh_cache(scheduler: Scheduler, index: int, served: frozenset[str] = frozenset()) -> Iterator[None]:
    """Each run starts from an empty cache, so the same settings twice still run; the entries in
    `served` it still finds where they were."""
    kept = scheduler.cache
    fresh = kept.objects.with_name(f"{kept.objects.name}-{index}-{time.monotonic_ns()}")
    scheduler.cache = _Serving(fresh, kept.tmp, served, kept) if served else Cache(fresh, kept.tmp)
    try:
        yield
    finally:
        scheduler.cache = kept


# -- the report ----------------------------------------------------------------------------------


def megabytes(value: Any) -> str:
    return (
        f"{value:,.0f} MB"
        if isinstance(value, int | float) and not isinstance(value, bool)
        else "not measured"
    )


def yes_no(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "**no**")


def run_table(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        "| Settings | Device | Free before | Estimate | Measured peak | Ratio | Within tolerance "
        "| System estimate | System peak | System ratio | Seconds | Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        out.append(
            f"| {row['label']} | {row.get('device') or '–'} | {megabytes(row.get('free_before_mb'))} | "
            f"{megabytes(row.get('estimate_mb'))} | {megabytes(row.get('measured_mb'))} | "
            f"{row.get('ratio') or '–'} | {yes_no(row.get('holds'))} | "
            f"{megabytes(row.get('system_estimate_mb'))} | {megabytes(row.get('system_measured_mb'))} | "
            f"{row.get('system_ratio') or '–'} | {row.get('seconds') or '–'} | {row.get('status')} |"
        )
    failures = [r for r in rows if r.get("error")]
    for row in failures:
        error = row["error"] or {}
        out += ["", f"{row['label']} failed ({error.get('kind')}): {error.get('message')}"]
    return out


def card_of(profile: dict[str, Any], target: Target) -> dict[str, Any] | None:
    """The card of `profile` that `target` names; None when it names none (the processor)."""
    if target.card is None:
        return None
    return next((g for g in profile.get("gpus") or [] if g.get("index") == target.card), None)


def machine_of(runtimes: Runtimes, manifest: Manifest) -> dict[str, Any]:
    """Where a report ran, so the report alone says so: the machine's profile, and the card the
    node's runtime build uses here, as the fit finds it (None for the processor)."""
    profile = runtimes.profile(refresh=True)
    card = None
    if manifest.run.where == "runtime" and manifest.run.runtime:
        with contextlib.suppress(RuntimeMissing):
            card = card_of(profile, runtimes.device_target(manifest.run.runtime))
    return {"profile": runtimes.profile_summary(), "card": card}


def machine_line(record: dict[str, Any]) -> str | None:
    """The machine a report ran on, from the profile it recorded; None when it recorded none."""
    profile = record.get("profile")
    if not profile:
        return None
    total = (profile.get("system") or {}).get("total_mb")
    where = str(profile.get("os") or "an unknown system")
    if total:
        where += f" with {megabytes(total)} of memory"
    card = record.get("card") or {}
    gpus = profile.get("gpus") or []
    parts = [where, *([f"driver {profile.get('driver')}"] if gpus else [])]
    for gpu in gpus:
        mine = ", the card this node's build uses" if card and gpu.get("index") == card.get("index") else ""
        parts.append(
            f"card {gpu.get('index')}, {gpu.get('name')} (compute {gpu.get('capability')}, "
            f"{megabytes(gpu.get('vram_total_mb'))}){mine}"
        )
    if not card:
        why = (profile.get("nvidia") or {}).get("why")
        parts.append("no card for this node" + (f" ({why})" if why and not gpus else ""))
    return f"Machine: {'; '.join(parts)}."


def heading(record: dict[str, Any]) -> list[str]:
    """A report's first lines: what it measured, when, with which engine, on which machine, and in
    which graph."""
    out = [
        f"# Fit report: {record.get('node')}",
        "",
        f"{record.get('date')} · engine {record.get('engine')}",
        "",
    ]
    machine = machine_line(record)
    if machine:
        out += [machine, ""]
    if record.get("mode") == "graph":
        inputs = ", ".join(f"{port} {size}" for port, size in (record.get("inputs") or {}).items())
        out += [f"Step `{record.get('step')}` of `{record.get('graph')}`; inputs: {inputs or 'none'}.", ""]
    return out


def render(record: dict[str, Any]) -> str:
    """The report, as Markdown. A number the bench did not get is "not measured", never zero."""
    return "\n".join([*heading(record), "## Runs", "", *run_table(record.get("runs") or []), ""])


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower().replace("nvidia", "").replace("geforce", "")).strip("-")


def report_path(out: Path, record: dict[str, Any]) -> Path:
    """A folder gets `YYYY-MM-DD-<card>-<node>-fit.md`, `cpu` for the card when the node had none
    here, and a number after `fit` when that name is taken, so a report never replaces another. A
    file is used as it is."""
    if out.suffix.lower() == ".md":
        return out
    card = (record.get("card") or {}).get("name")
    where = _slug(str(card)) if card else ("cpu" if record.get("profile") else "")
    name = "-".join(part for part in (where, _slug(str(record.get("node") or ""))) if part) or "machine"
    stem = f"{str(record.get('date', ''))[:10]}-{name}-fit"
    path, number = out / f"{stem}.md", 2
    while path.exists():
        path, number = out / f"{stem}-{number}.md", number + 1
    return path


# -- the command ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench:fit", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("node", help="the node to measure, or with --graph the key of its step")
    parser.add_argument(
        "--set", action="append", nargs="+", default=[], metavar="K=V", help="one run's params"
    )
    parser.add_argument(
        "--graph",
        type=Path,
        default=None,
        help="a graph file holding the step to measure, so it gets real inputs",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="a report file, or a folder for it (default: <data>/reports/)"
    )
    parser.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
    parser.add_argument(
        "--nodes", type=Path, action="append", default=None, help="node folders (default: nodes/)"
    )
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="runtime definitions")
    parser.add_argument("--uv", default=None)
    parser.add_argument("--uv-home", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        data, warnings = open_root(args.data.resolve() if args.data else None, version=__version__)
    except RootRefused as exc:
        print(exc, file=sys.stderr)
        return 1
    data = data.resolve()
    for warning in warnings:
        print(warning, file=sys.stderr)
    manager = Runtimes(data, args.runtimes, uv=find_uv(args.uv), uv_home=args.uv_home)
    record: dict[str, Any] = {"date": datetime.now(UTC).isoformat(timespec="seconds"), "engine": __version__}
    # The tool's temporary files, its runs' cache and their jobs, all in its folder under the root.
    with for_tool(Layout(data)) as mine:
        work = mine.make("bench-fit-")
        registry = discover(args.nodes or [BUILTIN_NODES_DIR])
        graph = None
        node = args.node
        if args.graph is not None:
            try:
                graph = Graph.from_json(read_object(args.graph, BenchGraphError)[0])
            except (BenchGraphError, GraphError, AttributeError, KeyError, TypeError) as exc:
                print(f"{args.graph}: not a graph the bench can read: {exc}", file=sys.stderr)
                return 2
            if args.node not in graph.nodes:
                print(f"{args.graph} has no step called {args.node!r}.", file=sys.stderr)
                return 2
            node = graph.nodes[args.node].node
        if node not in registry.nodes:
            print(f"No node called {node!r}.", file=sys.stderr)
            return 2
        manifest = registry.get(node)
        try:
            groups = [parse_sets(group, manifest) for group in args.set]
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        scheduler = make_scheduler(data, registry, manager, work / "cache", tmp_root=mine.folder)
        record.update(machine_of(manager, manifest))
        if graph is None:
            record.update(bench_node(scheduler, node, groups))
        else:
            try:
                record.update(bench_in_graph(scheduler, graph, args.node, groups, str(args.graph)))
            except BenchGraphError as exc:
                print(str(exc), file=sys.stderr)
                return 1
    path = report_path(args.out or Layout(data).reports, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(record), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
