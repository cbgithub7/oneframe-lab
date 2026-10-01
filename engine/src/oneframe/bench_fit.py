"""The fit report: run a node at the settings given and show, in numbers, how its memory model held.

    npm run bench:fit -- <node> [--set k=v ...]... [--out <file or folder>]
    npm run bench:fit -- --out specs/002-fit-to-memory/reports/

With a node, it runs that node once per `--set` group (once at its defaults without one) and
records, for each: the estimate, the peak it measured, their ratio, the seconds, and the free memory
before the run. Spec 005 measures real models with it.

With no node, it runs spec 002's AC8 on this machine's card, in the torch runtime (installed if
needed), with the test node whose folder under engine/tests/hardware/ holds an `ac8.json` saying
how (which param to size, the overrun's params and so on), so nothing here names that node:

1. the CUDA context's size, from nvidia-smi before CUDA starts and after a first kernel;
2. the node at three settings sized to this card's budget, each estimate against its peak;
3. another program holding memory, and the fit the node gets;
4. an overrun past the cap: a control spill first (Windows), then the overrun answered by
   ctx.fallbacks and, with them off, by the engine's retry, while Windows' per-process shared GPU
   memory counter is sampled every 250 ms.

It writes a Markdown report of every number and says plainly what was not measured. The report
names the card because it is evidence; nothing here decides on that name. Nothing here imports
torch: the node and its helpers run in the runtime's own interpreter.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__, hardware, memory
from oneframe.cache import Cache
from oneframe.executors import NodeError, ProcessExecutor, child_env
from oneframe.graph import Graph
from oneframe.manifest import Manifest, Param, check_param
from oneframe.memory import LearnedStore, Settings
from oneframe.registry import Registry, discover
from oneframe.runtimes import InstallRefused, RuntimeMissing, Runtimes, find_uv
from oneframe.scheduler import Scheduler
from oneframe.server import default_data_dir

HARDWARE_NODES = Path(__file__).resolve().parents[2] / "tests" / "hardware"
AC8_FILE = "ac8.json"
TOLERANCE_SHARE = 0.10  # an estimate holds within 10% of the peak,
TOLERANCE_MB = 64  # or 64 MB, whichever is larger
SAMPLE_S = 0.25


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
                    outside = memory.evaluate(model.outside_torch, model, values, {})[0] or 0.0
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


def make_scheduler(
    registry: Registry,
    manager: Runtimes | None,
    cache_root: Path,
    settings: Callable[[], Settings] = Settings,
    on_pid: Callable[[int], None] | None = None,
    runtime_python: Callable[[str], Path] | None = None,
) -> Scheduler:
    """A scheduler for the bench: a cache of its own (a repeated setting must run, not be served),
    nothing learned (estimates stay uncorrected), and the person's settings left alone."""

    def machine(cards: bool) -> dict[str, Any]:
        return hardware.profile(manager.data if manager else None, cards=cards)

    kw: dict[str, Any] = {}
    if manager is not None:
        kw.update(
            runtime_python=manager.python_for, runtime_env=manager.env_for, target=manager.device_target
        )
    if runtime_python is not None:
        kw["runtime_python"] = runtime_python
    return Scheduler(
        registry,
        Cache(cache_root),
        machine=machine,
        settings=settings,
        learned=LearnedStore(None),
        on_pid=on_pid,
        log_dir=manager.data / "logs" if manager else None,
        **kw,
    )


def bench_node(scheduler: Scheduler, node: str, groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Any node at the settings given: one run per group."""
    rows = []
    for i, params in enumerate(groups or [{}]):
        with _fresh_cache(scheduler, i):
            rows.append(measure(scheduler, node, params))
    return {"mode": "node", "node": node, "runs": rows}


@contextlib.contextmanager
def _fresh_cache(scheduler: Scheduler, index: int) -> Iterator[None]:
    """Each run starts from an empty cache, so the same settings twice still run."""
    base = scheduler.cache.root
    scheduler.cache = Cache(base.parent / f"{base.name}-{index}-{time.monotonic_ns()}")
    try:
        yield
    finally:
        scheduler.cache = Cache(base)


# -- Windows' per-process shared GPU memory --------------------------------------------------------


class SharedMemorySampler:
    """Samples `\\GPU Process Memory(pid_<pid>_*)\\Shared Usage` every 250 ms through the Windows
    performance-counter API (pdh.dll, through ctypes). What a process has spilled into shared
    system memory shows here; nvidia-smi cannot say it under Windows' display driver. Elsewhere,
    or where the counter cannot be read, `why` says so and nothing is sampled."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, float]] = []  # (seconds since start, MB)
        self.why: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self, pid: int) -> None:
        if sys.platform != "win32":
            self.why = "Shared GPU memory is a Windows counter; not measured on this system."
            return
        self._thread = threading.Thread(target=self._sample, args=(pid,), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def growth_mb(self) -> float | None:
        if not self.samples:
            return None
        return max(mb for _t, mb in self.samples) - self.samples[0][1]

    def _sample(self, pid: int) -> None:
        try:
            read = _pdh_reader(pid)
        except OSError as exc:
            self.why = f"The counter could not be opened ({exc})."
            return
        started = time.monotonic()
        while not self._stop.is_set():
            try:
                value = read()
            except OSError as exc:
                self.why = f"The counter could not be read ({exc})."
                return
            if value is not None:
                self.samples.append((round(time.monotonic() - started, 2), value))
            self._stop.wait(SAMPLE_S)


def _pdh_reader(pid: int) -> Callable[[], float | None]:
    """A function returning the process's shared GPU memory in MB (summed over its adapters), or
    None while the process has no instance yet."""
    if sys.platform != "win32":
        raise OSError("pdh.dll exists only on Windows")
    import ctypes
    from ctypes import wintypes

    class Value(ctypes.Structure):
        class Union(ctypes.Union):
            _fields_ = (
                ("longValue", ctypes.c_long),
                ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_longlong),
            )

        _fields_ = (("CStatus", wintypes.DWORD), ("u", Union))

    class Item(ctypes.Structure):
        _fields_ = (("szName", wintypes.LPWSTR), ("FmtValue", Value))

    pdh = ctypes.WinDLL("pdh.dll")
    more_data = 0x800007D2
    fmt_large = 0x00000400
    query = wintypes.HANDLE()
    counter = wintypes.HANDLE()
    if pdh.PdhOpenQueryW(None, None, ctypes.byref(query)) != 0:
        raise OSError("PdhOpenQueryW failed")
    path = "\\GPU Process Memory(*)\\Shared Usage"
    status = pdh.PdhAddEnglishCounterW(query, path, None, ctypes.byref(counter))
    if status != 0:
        raise OSError(f"PdhAddEnglishCounterW failed with 0x{status & 0xFFFFFFFF:08X}")
    pdh.PdhCollectQueryData(query)
    prefix = f"pid_{pid}_"

    def read() -> float | None:
        if pdh.PdhCollectQueryData(query) != 0:
            return None
        size, count = wintypes.DWORD(0), wintypes.DWORD(0)
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, fmt_large, ctypes.byref(size), ctypes.byref(count), None
        )
        if status & 0xFFFFFFFF != more_data:
            return None
        buffer = (ctypes.c_byte * size.value)()
        status = pdh.PdhGetFormattedCounterArrayW(
            counter, fmt_large, ctypes.byref(size), ctypes.byref(count), buffer
        )
        if status != 0:
            return None
        items = ctypes.cast(buffer, ctypes.POINTER(Item))
        found = [
            items[i].FmtValue.u.largeValue for i in range(count.value) if items[i].szName.startswith(prefix)
        ]
        return sum(found) / 1e6 if found else None

    return read


# -- AC8 -------------------------------------------------------------------------------------------


def _card_device(profile: dict[str, Any], target: memory.Target) -> dict[str, Any] | None:
    return next((g for g in profile.get("gpus") or [] if g.get("index") == target.card), None)


@dataclass(frozen=True)
class Ac8:
    """How AC8 runs, from the test node's own folder (`ac8.json`)."""

    folder: Path
    node: str
    scale: str
    fixed: dict[str, Any]
    shares: tuple[float, ...]
    control: dict[str, Any]
    overrun: dict[str, Any]
    without_fallbacks: dict[str, Any]
    context: str
    holder: str


def find_ac8(root: Path = HARDWARE_NODES) -> Ac8:
    """The one folder under `root` with an ac8.json, read."""
    found = sorted(root.glob(f"*/{AC8_FILE}"))
    if len(found) != 1:
        raise FileNotFoundError(f"Expected one {AC8_FILE} under {root}; found {len(found)}.")
    folder = found[0].parent
    row = json.loads(found[0].read_text(encoding="utf-8"))
    node = json.loads((folder / "node.json").read_text(encoding="utf-8"))["id"]
    return Ac8(
        folder,
        node,
        row["scale"],
        dict(row["fixed"]),
        tuple(row["shares"]),
        dict(row["control"]),
        dict(row["overrun"]),
        dict(row["without_fallbacks"]),
        row["context"],
        row["holder"],
    )


def three_settings(
    manifest: Manifest, plan: Ac8, budget: float, card: dict[str, Any]
) -> list[dict[str, Any]]:
    """For each share, the largest value of the scale param (a multiple of 64) whose estimate, from
    the node's own memory model, is at most that share of this card's budget; so the settings
    follow the card the bench runs on rather than any one card."""
    model = manifest.memory
    param = manifest.params[plan.scale]
    assert model is not None
    low, high = int(param.minimum or 64), int(param.maximum or 16384)
    base = {**_defaults(manifest), **plan.fixed}
    out = []
    for share in plan.shares:
        target = share * budget
        best = low
        lo, hi = low // 64, high // 64
        while lo <= hi:  # the need grows with the param: find the last value that fits
            mid = (lo + hi) // 2
            need = _need(model, {**base, plan.scale: max(mid * 64, low)}, card)
            if need is not None and need <= target:
                best, lo = max(mid * 64, low), mid + 1
            else:
                hi = mid - 1
        out.append({**plan.fixed, plan.scale: best})
    return out


def _probe_context(manager: Runtimes, plan: Ac8, card: int) -> dict[str, Any]:
    python = manager.python_for("torch")
    file, _, function = plan.context.partition(":")
    with tempfile.TemporaryDirectory(prefix="oneframe-context-") as out:
        job = {
            "run": "bench",
            "step": "context",
            "node": "probe:context",
            "entry": {"file": str(plan.folder / file), "function": function},
            "inputs": {},
            "params": {"card": card},
            "out_dir": out,
            "device": "cpu",  # no cap: the probe starts CUDA itself, after its first reading
        }
        try:
            done = ProcessExecutor(python, env=manager.env_for("torch")).execute(
                job, lambda _e: None, lambda: False
            )
        except NodeError as exc:
            return {"why": f"{exc.kind}: {exc}"}
    return dict(done.get("stats") or {})


@contextlib.contextmanager
def _holding(manager: Runtimes, plan: Ac8, mb: float) -> Iterator[None]:
    """Another program holding `mb` on the card while the block runs."""
    proc = subprocess.Popen(
        [str(manager.python_for("torch")), str(plan.folder / plan.holder), str(round(mb))],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        env=child_env(None, manager.env_for("torch")),
    )
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline().strip()
        if line != "ready":
            raise RuntimeError(f"The holder process did not start (it said {line!r}).")
        yield
    finally:
        if proc.stdin is not None:
            proc.stdin.close()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def _sampled(scheduler: Scheduler, node: str, params: dict[str, Any], label: str) -> dict[str, Any]:
    """A run with the shared-memory counter sampled for each process it starts."""
    samplers: list[SharedMemorySampler] = []

    def watch(pid: int) -> None:
        sampler = SharedMemorySampler()
        samplers.append(sampler)
        sampler.start(pid)

    scheduler.on_pid = watch
    try:
        row = measure(scheduler, node, params, label)
    finally:
        scheduler.on_pid = None
        for sampler in samplers:
            sampler.stop()
    growth = [s.growth_mb for s in samplers]
    row["shared_growth_mb"] = None if any(g is None for g in growth) or not growth else max(growth)  # type: ignore[type-var]
    row["shared_why"] = next((s.why for s in samplers if s.why), None)
    row["shared_samples"] = [s.samples for s in samplers]
    return row


def bench_ac8(manager: Runtimes, cache_root: Path) -> dict[str, Any]:
    """Everything AC8's report shows, as data."""
    plan = find_ac8()
    record: dict[str, Any] = {"mode": "ac8", "node": plan.node}
    try:
        installed = manager.install("torch", emit=_progress)
    except (InstallRefused, RuntimeMissing) as exc:
        record["why"] = f"The torch runtime could not be installed: {exc}"
        return record
    if installed is None:
        record["why"] = "The torch runtime's install did not finish."
        return record
    target = manager.device_target("torch")
    profile = manager.profile(refresh=True)
    record["profile"] = {k: v for k, v in profile.items() if k != "raw"}
    card = _card_device(profile, target)
    if card is None:
        record["why"] = (
            "The torch runtime's build here has no card, so there is nothing on a card to measure."
        )
        return record
    record["card"] = card
    registry = discover([plan.folder.parent])
    scheduler = make_scheduler(registry, manager, cache_root)
    manifest = registry.get(plan.node)
    model = manifest.memory
    assert model is not None

    _say("1/4 the CUDA context's size")
    record["context"] = _probe_context(manager, plan, int(card["index"]))

    _say("2/4 three settings")
    fresh = _card_device(hardware.profile(manager.data), target) or card
    margin = memory.margin(Settings(), "cuda", fresh.get("vram_total_mb"))
    budget = (fresh.get("vram_free_mb") or 0) - margin
    record["settings"] = []
    for i, params in enumerate(three_settings(manifest, plan, budget, fresh)):
        with _fresh_cache(scheduler, i):
            record["settings"].append(measure(scheduler, plan.node, params))

    _say("3/4 another program holding memory")
    record["holder"] = _bench_holder(manager, scheduler, plan, target)

    _say("4/4 an overrun past the cap")
    record["overrun"] = _bench_overrun(manager, scheduler, plan, target)
    return record


def _need(model: memory.MemoryModel, values: dict[str, Any], card: dict[str, Any]) -> float | None:
    device = memory.Device(
        "cuda", "cuda", card.get("capability"), card.get("vram_free_mb"), card.get("vram_total_mb"), 0
    )
    return memory.estimate(model, values, {}, device, memory.Learned()).device


def _defaults(manifest: Manifest) -> dict[str, Any]:
    return {name: p.default for name, p in manifest.params.items()}


def _label(params: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def _bench_holder(
    manager: Runtimes, scheduler: Scheduler, plan: Ac8, target: memory.Target
) -> dict[str, Any]:
    """Hold memory so the budget leaves room only for the node after its first change, then run it
    at its defaults and record the fit it was given."""
    card = _card_device(hardware.profile(manager.data), target)
    if card is None or card.get("vram_free_mb") is None:
        return {"why": "The card's free memory could not be read."}
    manifest = scheduler.registry.get(plan.node)
    model = manifest.memory
    assert model is not None
    after_first = {**_defaults(manifest), **model.changes[0].set}
    need = _need(model, after_first, card) or 0.0
    margin = memory.margin(Settings(), "cuda", card.get("vram_total_mb"))
    hold = float(card["vram_free_mb"]) - (need + 32 + margin)  # a budget of the need, plus 32 MB
    if hold <= 0:
        return {
            "why": f"The card has too little free memory to hold any back ({card['vram_free_mb']} MB free)."
        }
    with _holding(manager, plan, hold), _fresh_cache(scheduler, 100):
        row = measure(scheduler, plan.node, {}, "defaults, with memory held")
    row["held_mb"] = round(hold)
    row["expected_changes"] = [0]
    return row


def _bench_overrun(
    manager: Runtimes, scheduler: Scheduler, plan: Ac8, target: memory.Target
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    manifest = scheduler.registry.get(plan.node)
    model = manifest.memory
    assert model is not None
    defaults = _defaults(manifest)
    card = _card_device(hardware.profile(manager.data), target)
    if card is None or card.get("vram_free_mb") is None:
        return {"why": "The card's free memory could not be read."}

    # The control: a deliberate spill, with the fit off and the cap lifted by the node itself.
    scheduler.settings = lambda: Settings(fit="off")
    with _fresh_cache(scheduler, 200):
        out["control"] = _sampled(scheduler, plan.node, plan.control, f"control: {_label(plan.control)}")

    # The overrun: a budget of the estimate (plus the tolerance), and more than that on attempt 1.
    need = _need(model, defaults, card) or 0.0
    free = float((_card_device(hardware.profile(manager.data), target) or card)["vram_free_mb"] or 0)
    margin_mb = max(free - (need + TOLERANCE_MB), 0.0)
    scheduler.settings = lambda: Settings(margin_mb={"cuda": margin_mb, "cpu": None})
    out["budget_mb"] = round(free - margin_mb)
    out["estimate_mb"] = round(need)
    with _fresh_cache(scheduler, 201):
        out["fallbacks"] = _sampled(scheduler, plan.node, plan.overrun, "overrun, with ctx.fallbacks")
    with _fresh_cache(scheduler, 202):
        retry = {**plan.overrun, **plan.without_fallbacks}
        out["retry"] = _sampled(scheduler, plan.node, retry, "overrun, with the engine's retry")
    out["overrun_params"] = plan.overrun
    scheduler.settings = Settings
    return out


# -- the report ----------------------------------------------------------------------------------


def _mb(value: Any) -> str:
    return (
        f"{value:,.0f} MB"
        if isinstance(value, int | float) and not isinstance(value, bool)
        else "not measured"
    )


def _yes(value: bool | None) -> str:
    return "unknown" if value is None else ("yes" if value else "**no**")


def _run_table(rows: list[dict[str, Any]]) -> list[str]:
    out = [
        "| Settings | Device | Free before | Estimate | Measured peak | Ratio | Within tolerance | Seconds "
        "| Status |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        out.append(
            f"| {row['label']} | {row.get('device') or '–'} | {_mb(row.get('free_before_mb'))} | "
            f"{_mb(row.get('estimate_mb'))} | {_mb(row.get('measured_mb'))} | {row.get('ratio') or '–'} | "
            f"{_yes(row.get('holds'))} | {row.get('seconds') or '–'} | {row.get('status')} |"
        )
    failures = [r for r in rows if r.get("error")]
    for row in failures:
        error = row["error"] or {}
        out += ["", f"{row['label']} failed ({error.get('kind')}): {error.get('message')}"]
    return out


def _changes(row: dict[str, Any]) -> str:
    fits = row.get("fits") or []
    return (
        "; ".join(
            f"attempt {f.get('attempt')}: {f.get('device')}, "
            f"changes {[c['change'] for c in f.get('changes') or []] or 'none'}"
            for f in fits
        )
        or "no fit"
    )


def render(record: dict[str, Any]) -> str:
    """The report, as Markdown. A number the bench did not get is "not measured", never zero."""
    head = [
        f"# Fit report: {record.get('node')}",
        "",
        f"{record.get('date')} · engine {record.get('engine')}",
        "",
    ]
    if record.get("mode") == "node":
        return "\n".join([*head, "## Runs", "", *_run_table(record.get("runs") or []), ""])
    if record.get("why"):
        return "\n".join([*head, f"Not measured: {record['why']}", ""])
    card = record.get("card") or {}
    context = record.get("context") or {}
    out = [
        *head,
        "## Machine",
        "",
        f"- Card {card.get('index')}: {card.get('name')}, compute capability {card.get('capability')}, "
        f"{_mb(card.get('vram_total_mb'))} total",
        f"- Driver: {(record.get('profile') or {}).get('driver')}",
        "",
        "## 1. The CUDA context",
        "",
        f"- Card used before CUDA started: {_mb(context.get('used_before_mb'))}",
        f"- After a first allocation and kernel: {_mb(context.get('used_after_mb'))}",
        f"- Torch's reserved memory then: {_mb(context.get('reserved_mb'))}",
        f"- **Context: {_mb(context.get('context_mb'))}** "
        "(the margin is the larger of 1,611 MB and 10% of the card)",
    ]
    if context.get("why"):
        out.append(f"- Not measured: {context['why']}")
    out += ["", "## 2. Three settings", "", *_run_table(record.get("settings") or [])]
    holder = record.get("holder") or {}
    out += ["", "## 3. Another program holding memory", ""]
    if holder.get("why"):
        out.append(f"Not measured: {holder['why']}")
    else:
        out += [
            f"- Held by another process: {_mb(holder.get('held_mb'))}",
            f"- The fit: {_changes(holder)} (expected change {holder.get('expected_changes')})",
            f"- Finished: {_yes(holder.get('status') == 'done')}",
            "",
            *_run_table([holder]),
        ]
    overrun = record.get("overrun") or {}
    out += ["", "## 4. An overrun past the cap", ""]
    if overrun.get("why"):
        out.append(f"Not measured: {overrun['why']}")
    else:
        control = overrun.get("control") or {}
        out += [
            f"- Budget {_mb(overrun.get('budget_mb'))} for an estimate of {_mb(overrun.get('estimate_mb'))}; "
            f"attempt 1 runs with {_label(overrun.get('overrun_params') or {})}",
            "- Control (a deliberate spill): shared GPU memory rose by "
            + _mb(control.get("shared_growth_mb"))
            + (f" ({control['shared_why']})" if control.get("shared_why") else ""),
        ]
        for key, what in (("fallbacks", "ctx.fallbacks"), ("retry", "the engine's retry")):
            row = overrun.get(key) or {}
            out.append(
                f"- With {what}: {row.get('status')}, {row.get('seconds')} s; "
                f"step_oom {len(row.get('steps_oom') or [])}, oom {len(row.get('ooms') or [])}; "
                f"{_changes(row)}; shared GPU memory rose by {_mb(row.get('shared_growth_mb'))}"
                + (f" ({row['shared_why']})" if row.get("shared_why") else "")
            )
    out += [
        "",
        "## Everything, as recorded",
        "",
        "```json",
        json.dumps(_trimmed(record), indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(out)


def _trimmed(record: dict[str, Any]) -> dict[str, Any]:
    """The record without the counter's raw samples, which only make the report long."""
    text = json.dumps(record, default=str)
    data = json.loads(text)
    for row in (data.get("overrun") or {}).values():
        if isinstance(row, dict):
            row.pop("shared_samples", None)
    return data


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


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _progress(event: dict[str, Any]) -> None:
    if event.get("event") == "runtime.step":
        _say(f"[{event['index']}/{event['total']}] {event['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench:fit", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("node", nargs="?", help="the node to measure; none runs spec 002's AC8")
    parser.add_argument(
        "--set", action="append", nargs="+", default=[], metavar="K=V", help="one run's params"
    )
    parser.add_argument("--out", type=Path, default=Path(), help="a report file, or a folder for it")
    parser.add_argument("--data", type=Path, default=None, help="data root (runtimes, logs)")
    parser.add_argument(
        "--nodes", type=Path, action="append", default=None, help="node folders (default: nodes/)"
    )
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="runtime definitions")
    parser.add_argument("--uv", default=None)
    parser.add_argument("--uv-home", type=Path, default=None)
    args = parser.parse_args(argv)

    data = (args.data or default_data_dir()).resolve()
    manager = Runtimes(data, args.runtimes, uv=find_uv(args.uv), uv_home=args.uv_home)
    record: dict[str, Any] = {"date": datetime.now(UTC).isoformat(timespec="seconds"), "engine": __version__}
    with tempfile.TemporaryDirectory(prefix="oneframe-bench-fit-") as scratch:
        if args.node is None:
            record.update(bench_ac8(manager, Path(scratch) / "cache"))
        else:
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
            scheduler = make_scheduler(registry, manager, Path(scratch) / "cache")
            record.update(bench_node(scheduler, args.node, groups))
    path = report_path(args.out, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(record), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
