"""The fit report: run a node at the settings given and show, in numbers, how its memory model held.

    npm run bench:fit -- <node> [--set k=v ...]... [--out <file or folder>]

It runs the node once per `--set` group (once at its defaults without one) and records, for each:
the estimate, the peak it measured, their ratio, the seconds, and the free memory before the run.
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
from oneframe.graph import Graph
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.manifest import Manifest, Param, check_param
from oneframe.memory.estimate import evaluate
from oneframe.memory.learned import LearnedStore, StoreKey
from oneframe.memory.settings import Settings
from oneframe.registry import Registry, discover
from oneframe.runtimes.manager import Runtimes, RuntimeSide, find_uv
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


def measure(scheduler: Scheduler, node: str, params: dict[str, Any], label: str = "") -> dict[str, Any]:
    """Run `node` once at `params`, from no cache, and return what the report shows of it."""
    events: list[dict[str, Any]] = []
    graph = Graph.from_json({"version": 1, "nodes": {"n": {"node": node, "params": params}}, "edges": []})
    started = time.monotonic()
    result = scheduler.run(graph, events.append)
    seconds = round(time.monotonic() - started, 2)
    fits = [e for e in events if e["event"] == "node.fit"]
    done = next((e for e in events if e["event"] == "node.done"), None)
    failed = next((e for e in events if e["event"] == "node.failed"), None)
    last = fits[-1] if fits else {}  # the fit of the attempt that finished, or failed last
    device = (done or {}).get("made_with", {}).get("device") or last.get("device")
    estimate = (last.get("needs") or {}).get("device")
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


@contextlib.contextmanager
def fresh_cache(scheduler: Scheduler, index: int) -> Iterator[None]:
    """Each run starts from an empty cache, so the same settings twice still run."""
    kept = scheduler.cache
    fresh = kept.objects.with_name(f"{kept.objects.name}-{index}-{time.monotonic_ns()}")
    scheduler.cache = Cache(fresh, kept.tmp)
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
        "| Settings | Device | Free before | Estimate | Measured peak | Ratio | Within tolerance | Seconds "
        "| Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        out.append(
            f"| {row['label']} | {row.get('device') or '–'} | {megabytes(row.get('free_before_mb'))} | "
            f"{megabytes(row.get('estimate_mb'))} | {megabytes(row.get('measured_mb'))} | "
            f"{row.get('ratio') or '–'} | {yes_no(row.get('holds'))} | {row.get('seconds') or '–'} | "
            f"{row.get('status')} |"
        )
    failures = [r for r in rows if r.get("error")]
    for row in failures:
        error = row["error"] or {}
        out += ["", f"{row['label']} failed ({error.get('kind')}): {error.get('message')}"]
    return out


def heading(record: dict[str, Any]) -> list[str]:
    """A report's first lines: what it measured, when, and with which engine."""
    return [
        f"# Fit report: {record.get('node')}",
        "",
        f"{record.get('date')} · engine {record.get('engine')}",
        "",
    ]


def render(record: dict[str, Any]) -> str:
    """The report, as Markdown. A number the bench did not get is "not measured", never zero."""
    return "\n".join([*heading(record), "## Runs", "", *run_table(record.get("runs") or []), ""])


def report_path(out: Path, record: dict[str, Any]) -> Path:
    """A folder gets `YYYY-MM-DD-<card>-fit.md`; a file is used as it is."""
    if out.suffix.lower() != ".md":
        name = str((record.get("card") or {}).get("name") or record.get("node") or "machine")
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower().replace("nvidia", "").replace("geforce", "")).strip(
            "-"
        )
        return out / f"{str(record.get('date', ''))[:10]}-{slug or 'machine'}-fit.md"
    return out


# -- the command ---------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench:fit", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("node", help="the node to measure")
    parser.add_argument(
        "--set", action="append", nargs="+", default=[], metavar="K=V", help="one run's params"
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
        if args.node not in registry.nodes:
            print(f"No node called {args.node!r}.", file=sys.stderr)
            return 2
        manifest = registry.get(args.node)
        try:
            groups = [parse_sets(group, manifest) for group in args.set]
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        scheduler = make_scheduler(data, registry, manager, work / "cache", tmp_root=mine.folder)
        record.update(bench_node(scheduler, args.node, groups))
    path = report_path(args.out or Layout(data).reports, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(record), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
