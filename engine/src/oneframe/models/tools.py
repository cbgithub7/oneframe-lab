"""The model store's command-line tools (spec 003, requirement 8), started by `scripts/tool.js`:

    npm run models:download -- [--nodes <folder>] <node> [--checkpoint c ... | --all]
    npm run models:pin -- [--nodes <folder>] <node>

Each opens its data root as the engine does (`--data`, else the dev root), keeps its journal there,
and works in its own folder under `cache/tmp/`. Ctrl+C is a Stop, as `models.stop` is: the download
ends within a moment, keeping its partial, and the next one resumes it.

A tool that finds the store held by another process says so and waits up to LOCK_WAIT_S for it to
let go before it is refused `locked`; Ctrl+C ends that wait with nothing started. On Windows, npm
gives the prompt back as soon as Ctrl+C reaches it, before the tool it ran has finished stopping,
so a download typed again at once would otherwise meet the store still held (spec 003, AC8's
report, finding 3). The engine's own `models.*` methods still refuse at once.

Exit codes: 0 done, 1 failed, 2 refused before starting, 130 stopped.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__
from oneframe.errors import Failure, Stopped
from oneframe.journal import engine_crash, journalled
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.models.jobs import Jobs, claim_store, hub_endpoint
from oneframe.models.pin import find_manifest, pin
from oneframe.models.store import Store, StoreRefused
from oneframe.node import Emit
from oneframe.registry import discover
from oneframe.scratch import for_tool

STOPPED = 130
PROGRESS_EVERY_S = 2.0
# How long a tool waits for a store another process holds. A tool stopped a moment ago usually lets
# go within 2 s; one stopped inside a TLS handshake or a name lookup can take longer, and then the
# refusal stands, as it did before the wait.
LOCK_WAIT_S = 10.0
LOCK_POLL_S = 0.1


def _print(text: str) -> None:
    print(text, file=sys.stderr, flush=True)


def _mb(size: Any) -> str:
    return f"{size / 1e6:.1f} MB" if isinstance(size, int | float) else "?"


def _shown(event: dict[str, Any], last: dict[str, float]) -> None:
    """One line for a person, per event that says something new."""
    kind = event.get("event", "")
    if kind == "model.progress" or kind == "pin.progress":
        now = time.monotonic()
        if now - last.get("progress", 0.0) < PROGRESS_EVERY_S:
            return
        last["progress"] = now
        total = event.get("total")
        share = f" ({100 * event['bytes'] / total:.0f}%)" if total else ""
        _print(f"  {event.get('file')}: {_mb(event['bytes'])} of {_mb(total)}{share}")
    elif kind in ("model.file", "pin.phase"):
        detail = {
            k: v for k, v in event.items() if k in ("hosts", "why", "wait_s", "bytes", "reason", "message")
        }
        extra = f" {json.dumps(detail)}" if detail else ""
        _print(f"  {event.get('file')}: {event.get('phase')}{extra}")
    elif kind in ("model.start",):
        _print(f"{event.get('what')} {event.get('node') or ''}: {len(event.get('files') or [])} files")
    elif kind in ("pin.resolved", "pin.file", "pin.drifted", "pin.failed"):
        _print(f"  {json.dumps({k: v for k, v in event.items() if k != 'event'})}")
    elif kind in ("model.done", "model.failed", "model.stopped"):
        for row in event.get("files") or []:
            why = f" ({row['failure']['message']})" if row.get("failure") else ""
            _print(f"  {row.get('name')}: {row.get('outcome')}{why}")
        _print(f"{kind}: {event.get('message', '')}".rstrip(": "))


def _claimed[T](claim: Callable[[], T]) -> tuple[T, bool] | None:
    """`claim()`, which takes the store's lock, and whether Ctrl+C was pressed while it ran, for the
    caller to stop what it started. While another process holds the store, say so and claim again
    every LOCK_POLL_S until LOCK_WAIT_S has passed; then the refusal stands, `locked` as before. A
    refused claim holds nothing, so the wait keeps the store from no one. Ctrl+C is held back
    meanwhile, so it never lands halfway through a claim; it ends the wait, and then this gives
    None, with nothing started."""
    pressed: list[bool] = []  # appended to by the signal handler: no lock, which it could deadlock on
    main = threading.current_thread() is threading.main_thread()
    previous = signal.signal(signal.SIGINT, lambda *_: pressed.append(True)) if main else None
    try:
        deadline = time.monotonic() + LOCK_WAIT_S
        said = False
        while not pressed:
            try:
                return claim(), bool(pressed)
            except StoreRefused as exc:
                if exc.reason != "locked" or time.monotonic() >= deadline:
                    raise
            if not said:
                _print(f"Another process is using the model store; waiting up to {LOCK_WAIT_S:g} s for it.")
                said = True
            time.sleep(LOCK_POLL_S)
        _print("Stopped while waiting; nothing was started.")
        return None
    finally:
        if main:
            signal.signal(signal.SIGINT, previous if previous is not None else signal.SIG_DFL)


def _wait(thread: threading.Thread, stop: Callable[[], object], pressed: bool = False) -> bool:
    """Wait for `thread`; Ctrl+C asks it to stop, as models.stop does, and so does `pressed`, a
    Ctrl+C that came while it was being started. Whether Ctrl+C was pressed."""
    if pressed:
        _print("Stopping; the partial download is kept, and the next download resumes it.")
        stop()
    while thread.is_alive():
        try:
            thread.join(0.2)
        except KeyboardInterrupt:
            if not pressed:
                _print("Stopping; the partial download is kept, and the next download resumes it.")
            pressed = True
            stop()
    return pressed


def download(
    data: Path,
    roots: list[Path],
    node: str,
    checkpoints: list[str] | str | None,
    transfer: dict[str, Any] | None = None,
) -> int:
    """`transfer`: options for each download (a test's TLS context, policy or proxy)."""
    registry = discover(roots)
    if node not in registry.nodes:
        why = next((p for path, p in registry.problems.items() if Path(path).parent.name == node), None)
        _print(f"No node called {node!r} can be loaded" + (f": {'; '.join(why)}" if why else "."))
        return 2
    ended: list[dict[str, Any]] = []
    last: dict[str, float] = {}

    def say(event: dict[str, Any]) -> None:
        _shown(event, last)
        if event.get("event") in ("model.done", "model.failed", "model.stopped"):
            ended.append(event)

    jobs = Jobs(data, say, transfer)
    try:
        claimed = _claimed(lambda: jobs.download(registry.get(node), checkpoints))
    except Failure as exc:
        _print(f"{exc} {exc.next or ''}".strip())
        return 2
    if claimed is None:
        return STOPPED
    thread = jobs._thread
    assert thread is not None
    _wait(thread, jobs.stop, pressed=claimed[1])
    end = ended[-1]["event"] if ended else "model.failed"
    return {"model.done": 0, "model.stopped": STOPPED}.get(end, 1)


def pin_tool(data: Path, roots: list[Path], node: str, transfer: dict[str, Any] | None = None) -> int:
    path = find_manifest(roots, node)
    if path is None:
        _print(f"No node called {node!r} in {', '.join(str(r) for r in roots)}.")
        return 2
    store = Store(data)
    try:
        claimed = _claimed(lambda: claim_store(store, None))
    except Failure as exc:
        _print(f"{exc} {exc.next or ''}".strip())
        return 2
    if claimed is None:
        return STOPPED
    held, pressed = claimed
    stop = threading.Event()
    result: dict[str, Any] = {}
    last: dict[str, float] = {}

    def work() -> None:
        def crashed(exc: BaseException) -> dict[str, Any]:
            return engine_crash(exc, event="pin.failed", node=node)

        try:
            with journalled(
                lambda e: _shown(e, last), Layout(data).journals, f"model-pin-{node}", crashed
            ) as emit:
                # A Stop and a declared failure end the journal as what they are; only a bug ends
                # it with an engine crash (journalled's own last line).
                try:
                    result["report"] = _pin(path, store, stop, emit, transfer)
                except Stopped:
                    result["stopped"] = True
                    emit({"event": "pin.stopped", "node": node, "message": "Stopped; pin again to carry on."})
                except Failure as exc:
                    result["failure"] = exc.to_json()
                    emit({"event": "pin.failed", "node": node, **result["failure"]})
        except Exception as exc:
            result["failure"] = crashed(exc)
        finally:
            held.release()

    thread = threading.Thread(target=work, name="pin", daemon=True)
    thread.start()
    _wait(thread, stop.set, pressed)
    if result.get("stopped"):
        _print("pin stopped; run it again to carry on.")
        return STOPPED
    if "failure" in result:
        _print(f"pin failed: {result['failure'].get('message')}")
        return 1
    report = result["report"]
    print(json.dumps(report.to_json(), indent=2))
    for row in report.changed:
        _print(f"{row['name']}: sha256 changed with the new pin, {row['old']} -> {row['new']}")
    for row in report.drifted:
        _print(
            f"{row['name']}: the source now gives {row['found']} under the same pin, not {row['recorded']}; "
            "the pin was left as it was"
        )
    for problem in report.problems:
        _print(f"{path}: {problem}")
    return 1 if report.failed or report.problems else 0


def _pin(path: Path, store: Store, stop: threading.Event, emit: Emit, transfer: dict[str, Any] | None) -> Any:
    emit({"event": "pin.start", "manifest": str(path)})
    report = pin(path, store, hub_endpoint(), stop, emit, transfer)
    emit({"event": "pin.done", **report.to_json()})
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="models", description=(__doc__ or "").split("\n\n")[0])
    tools = parser.add_subparsers(dest="tool", required=True)
    for name in ("download", "pin"):
        sub = tools.add_parser(name)
        sub.add_argument("node")
        sub.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
        sub.add_argument(
            "--nodes", type=Path, action="append", default=None, help="a folder of nodes (default: nodes/)"
        )
        if name == "download":
            group = sub.add_mutually_exclusive_group()
            group.add_argument(
                "--checkpoint", action="append", default=None, help="a checkpoint; repeat for more"
            )
            group.add_argument("--all", action="store_true", help="every checkpoint")
    args = parser.parse_args(argv)
    try:
        data, warnings = open_root(args.data.resolve() if args.data else None, version=__version__)
    except RootRefused as exc:
        _print(f"{exc} {exc.next or ''}".strip())
        return 2
    for warning in warnings:
        _print(warning)
    roots = [p.resolve() for p in args.nodes] if args.nodes else [BUILTIN_NODES_DIR]
    with for_tool(Layout(data)):
        if args.tool == "download":
            return download(data, roots, args.node, "all" if args.all else args.checkpoint)
        return pin_tool(data, roots, args.node)


if __name__ == "__main__":
    raise SystemExit(main())
