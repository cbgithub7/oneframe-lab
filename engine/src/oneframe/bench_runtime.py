"""The runtime report: install a runtime on this machine and show, in numbers, what it did.

    npm run bench:runtime -- <runtime> [--out <file>]

It reads the machine, plans, installs (timed), runs the runtime's probe inside it with the network
closed, and writes a Markdown report: the machine as nvidia-smi printed it, the plan with every
build it considered, the install's time and size, the torch lines of the freeze, and what the probe
returned. This is the evidence a hardware claim needs (AGENTS.md: nothing is called working on a
GPU without numbers from a real run), so it records what happened, including failures.

Nothing here names a runtime or imports torch: the probe is the runtime's own code, run in its own
interpreter.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oneframe import __version__, runtime_install
from oneframe.executors import NodeError, ProcessExecutor
from oneframe.layout import Layout, default_root
from oneframe.runtimes import InstallRefused, RuntimeMissing, Runtimes, find_uv


def _probe(manager: Runtimes, runtime_id: str) -> dict[str, Any]:
    runtime = manager.get(runtime_id)
    if not runtime.probe:
        return {"ran": False, "why": "The runtime has no probe."}
    python = manager.python_for(runtime_id)
    build = runtime.build(str(manager.plan_for(runtime).build))
    file, _, function = runtime.probe.partition(":")
    with tempfile.TemporaryDirectory(prefix="oneframe-probe-") as out:
        job = {
            "run": "bench",
            "step": "probe",
            "node": f"probe:{runtime_id}",
            "entry": {"file": str(runtime.folder / file), "function": function},
            "inputs": {},
            "params": {},
            "out_dir": out,
            "device": "cuda" if build is not None and build.vendor == "nvidia" else "cpu",
        }
        executor = ProcessExecutor(python, env=manager.env_for(runtime_id), log_dir=manager.data / "logs")
        try:
            done = executor.execute(job, lambda _e: None, lambda: False)
        except NodeError as exc:
            return {"ran": False, "why": f"{exc.kind}: {exc}", "detail": exc.detail[-2000:]}
    return {
        "ran": True,
        "seconds": done.get("seconds"),
        "peak_vram_mb": done.get("peak_vram_mb"),
        "result": done.get("stats") or {},
    }


def bench(manager: Runtimes, runtime_id: str) -> dict[str, Any]:
    """Everything the report shows, as data."""
    profile = manager.profile(refresh=True)
    runtime = manager.get(runtime_id)
    the_plan = manager.plan_for(runtime)
    record: dict[str, Any] = {
        "runtime": runtime_id,
        "date": datetime.now(UTC).isoformat(timespec="seconds"),
        "engine": __version__,
        "profile": profile,
        "plan": the_plan.to_json(),
    }
    lines: list[str] = []

    def progress(event: dict[str, Any]) -> None:
        if event.get("event") == "runtime.step":
            print(f"[{event['index']}/{event['total']}] {event['message']}", file=sys.stderr, flush=True)
        elif event.get("event") == "runtime.log":
            lines.append(str(event.get("line")))
        elif event.get("event") == "runtime.failed":
            record["install"] = {"ok": False, "message": event.get("message"), "detail": event.get("detail")}

    started = time.monotonic()
    try:
        done = manager.install(runtime_id, emit=progress)
    except (InstallRefused, RuntimeMissing) as exc:
        record["install"] = {"ok": False, "message": str(exc)}
        return record
    if done is None:
        record.setdefault("install", {"ok": False, "message": "The install did not finish."})
        return record
    build = str(the_plan.build)
    marker = runtime_install.read_marker(runtime_install.env_dir(manager.data, runtime_id, build)) or {}
    record["install"] = {
        "ok": True,
        "already": bool(done.get("already")),
        "seconds": None if done.get("already") else round(time.monotonic() - started, 1),
        "build": build,
        "python": marker.get("python"),
        "uv": marker.get("uv"),
        "size_bytes": marker.get("size_bytes"),
        "freeze": marker.get("freeze") or [],
    }
    record["probe"] = _probe(manager, runtime_id)
    return record


def _mb(size: Any) -> str:
    """Bytes as MB, or "unknown": a report never turns a missing number into zero."""
    return (
        f"{size / 1e6:,.0f} MB" if isinstance(size, int | float) and not isinstance(size, bool) else "unknown"
    )


def _from_mb(size: Any) -> str:
    return _mb(size * 1e6) if isinstance(size, int | float) and not isinstance(size, bool) else "unknown"


def render(record: dict[str, Any]) -> str:
    profile = record["profile"]
    plan = record["plan"]
    install = record.get("install") or {}
    out = [
        f"# Runtime report: {record['runtime']}",
        "",
        f"{record['date']} · {profile.get('os')} · engine {record['engine']}",
        "",
        "## Machine",
        "",
    ]
    for gpu in profile.get("gpus") or []:
        out.append(
            f"- Card {gpu['index']}: {gpu['name']}, compute capability {gpu['capability'] or 'unknown'}, "
            f"{_from_mb(gpu.get('vram_total_mb'))} total"
        )
    if not profile.get("gpus"):
        out.append(f"- No NVIDIA card: {profile.get('nvidia', {}).get('why')}")
    system = profile.get("system") or {}
    out += [
        f"- Driver: {profile.get('driver') or 'none'}",
        f"- System memory: {_from_mb(system.get('free_mb'))} free of {_from_mb(system.get('total_mb'))}"
        + (f" ({system['why']})" if system.get("why") else ""),
        f"- Free disk on the data root: {_from_mb(profile.get('disk_free_mb'))}",
        "",
        "nvidia-smi said:",
        "",
        "```",
        (profile.get("raw") or "(nothing: nvidia-smi was not run)").strip(),
        "```",
        "",
        "## Plan",
        "",
        f"**{plan['build'] or 'blocked'}**: {plan['why']}",
        "",
        "| Build | Chosen | Why |",
        "| --- | --- | --- |",
    ]
    out += [
        f"| {row['build']} | {'yes' if row['chosen'] else 'no'} | {row['why']} |"
        for row in plan["considered"]
    ]
    if plan.get("notes"):
        out += ["", *(f"- {note}" for note in plan["notes"])]
    out += ["", "## Install", ""]
    if install.get("ok"):
        took = "already installed, not timed" if install.get("already") else f"{install.get('seconds')} s"
        torch = [line for line in install.get("freeze") or [] if line.lower().startswith("torch")]
        out += [
            f"- Build: {install.get('build')}, Python {install.get('python')}, {install.get('uv')}",
            f"- Time: {took}",
            f"- Size: {_mb(install.get('size_bytes'))}",
            f"- Packages: {len(install.get('freeze') or [])}",
            *(f"- `{line}`" for line in torch),
        ]
    else:
        out += [
            f"- Failed: {install.get('message')}",
            "",
            "```",
            str(install.get("detail") or "").strip(),
            "```",
        ]
    probe = record.get("probe")
    if probe is not None:
        out += ["", "## Probe", ""]
        if probe.get("ran"):
            peak = probe.get("peak_vram_mb")
            out += [
                f"- Seconds: {probe.get('seconds')}",
                f"- Peak VRAM: {peak} MB" if peak is not None else "- Peak VRAM: none (no card was used)",
                "",
                "```json",
                json.dumps(probe.get("result"), indent=2),
                "```",
            ]
        else:
            out += [f"- Did not run: {probe.get('why')}"]
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bench:runtime", description="Install a runtime and report what it did."
    )
    parser.add_argument("runtime", help="the runtime's id, a folder in runtimes/")
    parser.add_argument("--out", type=Path, default=None, help="the report (default: <data>/reports/)")
    parser.add_argument("--data", type=Path, default=None, help="the data root (default: the dev root)")
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="a folder of runtimes")
    parser.add_argument("--uv", default=None)
    parser.add_argument("--uv-home", type=Path, default=None)
    args = parser.parse_args(argv)
    data = args.data or default_root()
    data.mkdir(parents=True, exist_ok=True)
    manager = Runtimes(data, args.runtimes, uv=find_uv(args.uv), uv_home=args.uv_home)
    try:
        record = bench(manager, args.runtime)
    except RuntimeMissing as exc:  # no such runtime: nothing to report on
        print(exc, file=sys.stderr)
        return 1
    out = args.out or Layout(data).reports / f"runtime-{args.runtime}-{record['date'][:10]}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(record), encoding="utf-8")
    print(f"The report is at {out}", file=sys.stderr)
    return 0 if (record.get("install") or {}).get("ok") and (record.get("probe") or {}).get("ran") else 1


if __name__ == "__main__":
    raise SystemExit(main())
