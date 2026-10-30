"""Spec 002's check on a card (its AC8), run with the test node beside this file:

    npm run bench:fit-check [-- --out <file or folder>]

It finds the one folder under hardware/ that holds an `ac8.json` saying how (which param to size,
the overrun's params and so on), and takes the runtime from that node's manifest (installed if
needed), so nothing in the engine names the node, its runtime or this folder (spec 013):

1. the CUDA context's size, from nvidia-smi before CUDA starts and after a first kernel;
2. the node at three settings sized to this card's budget, each estimate against its peak;
3. another program holding memory, so the budget has room for the node after its first change but
   not at its defaults, and the fit the node gets;
4. an overrun past the cap, answered by ctx.fallbacks and, with them off, by the engine's retry.
   torch's own out-of-memory message shows, for each, the request, the card's free memory then
   and the cap: a request the card had room for was refused by the cap before the driver was
   asked, so it could not spill into shared memory.

Each run is bench:fit's (`oneframe.bench_fit`): nothing is learned between them, so every estimate
is the node's own, uncorrected. It writes a Markdown report of every number, and says plainly what
was not measured and which step did not happen as designed. The report names the card because it
is evidence; nothing here decides on that name. Nothing here imports torch: the node and its
helpers run in the runtime's own interpreter.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oneframe import __version__, hardware
from oneframe.bench_fit import (
    TOLERANCE_MB,
    fresh_cache,
    heading,
    make_scheduler,
    measure,
    megabytes,
    report_path,
    run_table,
    yes_no,
)
from oneframe.environment import child_env
from oneframe.executors import NodeError, run_probe
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.manifest import Manifest
from oneframe.memory.estimate import Device, Learned, Target
from oneframe.memory.estimate import estimate as estimate_need
from oneframe.memory.model import MemoryModel
from oneframe.memory.settings import Settings
from oneframe.memory.settings import margin as margin_for
from oneframe.processes import Child
from oneframe.registry import discover
from oneframe.runtimes.manager import InstallRefused, RuntimeMissing, Runtimes, find_uv
from oneframe.scheduler import Scheduler
from oneframe.scratch import for_tool

HARDWARE_NODES = Path(__file__).resolve().parent / "hardware"
AC8_FILE = "ac8.json"


# -- torch's out-of-memory message ---------------------------------------------------------------

_SIZE = r"([\d.]+) (bytes|KiB|MiB|GiB)"
_BYTES = {"bytes": 1, "KiB": 1024, "MiB": 1024**2, "GiB": 1024**3}


def cap_refusal(message: str | None) -> dict[str, float] | None:
    """What torch's out-of-memory message says when its capped allocator refused a request: the
    request, the card's free memory then, and the cap ("allowed"), in MB. None when the message
    is not torch's or names no cap.

    torch checks the cap before it asks the driver for memory, so a request the card had room
    for was refused by the cap, and could not have spilled into shared memory."""
    if not message:
        return None
    found = [
        re.search(pattern, message)
        for pattern in (rf"Tried to allocate {_SIZE}", rf"of which {_SIZE} is free", rf"{_SIZE} allowed;")
    ]
    if not all(found):
        return None
    request, free, allowed = (
        round(float(m.group(1)) * _BYTES[m.group(2)] / 1e6, 1) for m in found if m is not None
    )
    return {"request_mb": request, "card_free_mb": free, "allowed_mb": allowed}


# -- the check -----------------------------------------------------------------------------------


def _card_device(profile: dict[str, Any], target: Target) -> dict[str, Any] | None:
    return next((g for g in profile.get("gpus") or [] if g.get("index") == target.card), None)


@dataclass(frozen=True)
class Ac8:
    """How AC8 runs, from the test node's own folder (`ac8.json`)."""

    folder: Path
    node: str
    runtime: str
    scale: str
    fixed: dict[str, Any]
    shares: tuple[float, ...]
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
    manifest = discover([folder.parent]).get(node)  # the manifest as the engine reads it
    if manifest.run.runtime is None:
        raise ValueError(f"{manifest.id} runs in the engine; the check needs a node that runs in a runtime.")
    return Ac8(
        folder,
        manifest.id,
        manifest.run.runtime,
        row["scale"],
        dict(row["fixed"]),
        tuple(row["shares"]),
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
    try:
        # On the processor, so no cap: the probe starts CUDA itself, after its first reading.
        done = run_probe(
            manager, plan.runtime, plan.folder, plan.context, device="cpu", params={"card": card}
        )
    except NodeError as exc:
        return {"why": f"{exc.kind}: {exc}"}
    return dict(done.stats)


@contextlib.contextmanager
def _holding(manager: Runtimes, plan: Ac8, mb: float, tmp: Path) -> Iterator[None]:
    """Another program holding `mb` on the card while the block runs, with its temporary files in
    `tmp`. Once ready, it lets go when
    its stdin closes; one that has not ended 30 s later, or never got ready, is killed, with
    anything it started."""
    with Child.start(
        [str(manager.python_for(plan.runtime)), str(plan.folder / plan.holder), str(round(mb))],
        env=child_env(
            None,
            manager.env_for(plan.runtime),
            caches=manager.caches_for(plan.runtime),
            tmp=tmp,
        ),
        stdin="pipe",
        stderr=_say,
    ) as holder:
        line = next(holder.lines(threading.Event()), "").strip()
        if line != "ready":
            raise RuntimeError(f"The holder process did not start (it said {line!r}).")
        try:
            yield
        finally:
            holder.close_stdin()
            holder.wait(30)


def check(manager: Runtimes, cache_root: Path, tmp: Path) -> dict[str, Any]:
    """Everything the check's report shows, as data. Its runs and the holder keep their temporary
    files in `tmp`."""
    plan = find_ac8()
    record: dict[str, Any] = {"mode": "ac8", "node": plan.node}
    try:
        installed = manager.install(plan.runtime, emit=_progress)
    except (InstallRefused, RuntimeMissing) as exc:
        record["why"] = f"The {plan.runtime} runtime could not be installed: {exc}"
        return record
    if installed is None:
        record["why"] = f"The {plan.runtime} runtime's install did not finish."
        return record
    target = manager.device_target(plan.runtime)
    profile = manager.profile(refresh=True)
    record["profile"] = {k: v for k, v in profile.items() if k != "raw"}
    card = _card_device(profile, target)
    if card is None:
        record["why"] = (
            f"The {plan.runtime} runtime's build here has no card, so there is nothing on a card to measure."
        )
        return record
    record["card"] = card
    registry = discover([plan.folder.parent])
    scheduler = make_scheduler(manager.data, registry, manager, cache_root, tmp_root=tmp)
    manifest = registry.get(plan.node)
    model = manifest.memory
    assert model is not None

    _say("1/4 the CUDA context's size")
    record["context"] = _probe_context(manager, plan, int(card["index"]))

    _say("2/4 three settings")
    fresh = _card_device(hardware.profile(manager.data), target) or card
    margin = margin_for(Settings(), "cuda", fresh.get("vram_total_mb"))
    budget = (fresh.get("vram_free_mb") or 0) - margin
    record["settings"] = []
    for i, params in enumerate(three_settings(manifest, plan, budget, fresh)):
        with fresh_cache(scheduler, i):
            record["settings"].append(measure(scheduler, plan.node, params))

    _say("3/4 another program holding memory")
    context_mb = record["context"].get("context_mb")
    record["holder"] = _bench_holder(manager, scheduler, plan, target, context_mb, tmp)

    _say("4/4 an overrun past the cap")
    record["overrun"] = _bench_overrun(manager, scheduler, plan, target)
    return record


def _need(model: MemoryModel, values: dict[str, Any], card: dict[str, Any]) -> float | None:
    device = Device(
        "cuda", "cuda", card.get("capability"), card.get("vram_free_mb"), card.get("vram_total_mb"), 0
    )
    return estimate_need(model, values, {}, device, Learned()).device


def _defaults(manifest: Manifest) -> dict[str, Any]:
    return {name: p.default for name, p in manifest.params.items()}


def _label(params: dict[str, Any]) -> str:
    return ", ".join(f"{k}={v}" for k, v in params.items())


def holder_size(
    manifest: Manifest, card: dict[str, Any], margin: float, context_mb: float | None
) -> tuple[float, tuple[float, float]] | None:
    """How much the holder holds, and the window the budget must then fall in: room for the node
    after its first change, not at its defaults. It aims at the window's middle, and counts the
    holder's own CUDA context (step 1's size), which it creates on top of what it holds. None when
    the first change lowers no need, so no budget calls for it."""
    model = manifest.memory
    assert model is not None
    defaults = _defaults(manifest)
    low = _need(model, {**defaults, **model.changes[0].set}, card)
    high = _need(model, defaults, card)
    if low is None or high is None or low >= high:
        return None
    hold = float(card["vram_free_mb"]) - margin - (low + high) / 2 - (context_mb or 0.0)
    return hold, (low, high)


def _bench_holder(
    manager: Runtimes,
    scheduler: Scheduler,
    plan: Ac8,
    target: Target,
    context_mb: float | None,
    tmp: Path,
) -> dict[str, Any]:
    """Hold memory so the budget leaves room only for the node after its first change, then run it
    at its defaults and record the fit it was given, and whether that showed the change."""
    card = _card_device(hardware.profile(manager.data), target)
    if card is None or card.get("vram_free_mb") is None:
        return {"why": "The card's free memory could not be read."}
    manifest = scheduler.registry.get(plan.node)
    margin = margin_for(Settings(), "cuda", card.get("vram_total_mb"))
    sized = holder_size(manifest, card, margin, context_mb)
    if sized is None:
        return {"why": "The node's first change lowers no need, so no budget calls for it."}
    hold, (low, high) = sized
    if hold <= 0:
        return {
            "why": f"The card has too little free memory to hold any back ({card['vram_free_mb']} MB free)."
        }
    with _holding(manager, plan, hold, tmp), fresh_cache(scheduler, 100):
        free = (_card_device(hardware.profile(manager.data), target) or {}).get("vram_free_mb")
        row = measure(scheduler, plan.node, {}, "defaults, with memory held")
    first = (row.get("fits") or [{}])[0]
    row["held_mb"] = round(hold)
    row["budget_mb"] = None if free is None else round(free - margin)
    row["window_mb"] = [round(low), round(high)]
    row["expected_changes"] = [0]
    row["shown"] = (
        row["status"] == "done"
        and first.get("device") == "cuda"
        and [c["change"] for c in first.get("changes") or []] == [0]
    )
    return row


def _bench_overrun(manager: Runtimes, scheduler: Scheduler, plan: Ac8, target: Target) -> dict[str, Any]:
    out: dict[str, Any] = {}
    manifest = scheduler.registry.get(plan.node)
    model = manifest.memory
    assert model is not None
    defaults = _defaults(manifest)
    card = _card_device(hardware.profile(manager.data), target)
    if card is None or card.get("vram_free_mb") is None:
        return {"why": "The card's free memory could not be read."}

    # The overrun: a budget of the estimate (plus the tolerance), and more than that on attempt 1.
    need = _need(model, defaults, card) or 0.0
    free = float((_card_device(hardware.profile(manager.data), target) or card)["vram_free_mb"] or 0)
    margin_mb = max(free - (need + TOLERANCE_MB), 0.0)
    scheduler.settings = lambda: Settings(margin_mb={"cuda": margin_mb, "cpu": None})
    out["budget_mb"] = round(free - margin_mb)
    out["estimate_mb"] = round(need)
    with fresh_cache(scheduler, 201):
        out["fallbacks"] = measure(scheduler, plan.node, plan.overrun, "overrun, with ctx.fallbacks")
    with fresh_cache(scheduler, 202):
        retry = {**plan.overrun, **plan.without_fallbacks}
        out["retry"] = measure(scheduler, plan.node, retry, "overrun, with the engine's retry")
    out["overrun_params"] = plan.overrun
    scheduler.settings = Settings
    for key, event in (("fallbacks", "steps_oom"), ("retry", "ooms")):
        row = out[key]
        first = (row.get("fits") or [{}])[0]
        # Exercised: attempt 1 ran on the card and its overrun met the cap as out of memory.
        row["exercised"] = first.get("device") == "cuda" and bool(row.get(event))
        refusal = cap_refusal(((row.get(event) or [{}])[0]).get("message"))
        row["refusal"] = refusal
        # By the cap: the card had room for the request, so the driver was never asked for it.
        row["by_cap"] = None if refusal is None else refusal["card_free_mb"] > refusal["request_mb"]
    return out


# -- the report ----------------------------------------------------------------------------------


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
    head = heading(record)
    if record.get("why"):
        return "\n".join([*head, f"Not measured: {record['why']}", ""])
    card = record.get("card") or {}
    context = record.get("context") or {}
    out = [
        *head,
        "## Machine",
        "",
        f"- Card {card.get('index')}: {card.get('name')}, compute capability {card.get('capability')}, "
        f"{megabytes(card.get('vram_total_mb'))} total",
        f"- Driver: {(record.get('profile') or {}).get('driver')}",
        "",
        "## 1. The CUDA context",
        "",
        f"- Card used before CUDA started: {megabytes(context.get('used_before_mb'))}",
        f"- After a first allocation and kernel: {megabytes(context.get('used_after_mb'))}",
        f"- Torch's reserved memory then: {megabytes(context.get('reserved_mb'))}",
        f"- **Context: {megabytes(context.get('context_mb'))}** "
        "(the margin is the larger of 1,611 MB and 10% of the card)",
    ]
    if context.get("why"):
        out.append(f"- Not measured: {context['why']}")
    out += ["", "## 2. Three settings", "", *run_table(record.get("settings") or [])]
    holder = record.get("holder") or {}
    out += ["", "## 3. Another program holding memory", ""]
    if holder.get("why"):
        out.append(f"Not measured: {holder['why']}")
    else:
        window = holder.get("window_mb") or [None, None]
        out += [
            f"- Held by another process: {megabytes(holder.get('held_mb'))}",
            f"- The card's budget then: {megabytes(holder.get('budget_mb'))}; the node needs "
            f"{megabytes(window[0])} after its first change and {megabytes(window[1])} at its defaults",
            f"- The fit: {_changes(holder)} (expected change {holder.get('expected_changes')})",
            f"- Finished: {yes_no(holder.get('status') == 'done')}",
            f"- Shown: {yes_no(holder.get('shown'))}"
            + (
                ""
                if holder.get("shown")
                else " (the fit did not make the expected change on the card, so this step shows "
                "nothing about it)"
            ),
            "",
            *run_table([holder]),
        ]
    overrun = record.get("overrun") or {}
    out += ["", "## 4. An overrun past the cap", ""]
    if overrun.get("why"):
        out.append(f"Not measured: {overrun['why']}")
    else:
        out.append(
            f"- Budget {megabytes(overrun.get('budget_mb'))} for an estimate of "
            f"{megabytes(overrun.get('estimate_mb'))}; "
            f"attempt 1 runs with {_label(overrun.get('overrun_params') or {})}"
        )
        for key, what in (("fallbacks", "ctx.fallbacks"), ("retry", "the engine's retry")):
            row = overrun.get(key) or {}
            out.append(
                f"- With {what}: {row.get('status')}, {row.get('seconds')} s; "
                f"step_oom {len(row.get('steps_oom') or [])}, oom {len(row.get('ooms') or [])}; "
                f"{_changes(row)}; {_refusal(row)}"
                + (
                    ""
                    if row.get("exercised")
                    else "; **not exercised**: attempt 1 did not run on the card into the cap, so this "
                    "run shows nothing about it"
                )
            )
    out += [
        "",
        "## Everything, as recorded",
        "",
        "```json",
        json.dumps(record, indent=2, default=str),
        "```",
        "",
    ]
    return "\n".join(out)


def _refusal(row: dict[str, Any]) -> str:
    """What torch's message shows about who refused the overrun: the cap, or a full card."""
    refusal = row.get("refusal")
    if refusal is None:
        return "**not shown**: torch's message did not give the request, the card's free memory and the cap"
    said = (
        f"torch refused {megabytes(refusal['request_mb'])} with {megabytes(refusal['card_free_mb'])} free "
        f"on the card and {megabytes(refusal['allowed_mb'])} allowed"
    )
    if row.get("by_cap"):
        return f"{said}: the cap refused it, so the driver was never asked and nothing could spill"
    return f"{said}: **the card had no room for it, so this does not show the cap refused it**"


# -- the command ---------------------------------------------------------------------------------


def _say(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _progress(event: dict[str, Any]) -> None:
    if event.get("event") == "runtime.step":
        _say(f"[{event['index']}/{event['total']}] {event['message']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench:fit-check", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument(
        "--out", type=Path, default=None, help="a report file, or a folder for it (default: <data>/reports/)"
    )
    parser.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
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
        record.update(check(manager, mine.make("fit-check-") / "cache", mine.folder))
    path = report_path(args.out or Layout(data).reports, record)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(record), encoding="utf-8")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
