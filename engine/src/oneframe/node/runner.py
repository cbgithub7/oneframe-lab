"""What runs one job, the same in the engine's process and in a runtime child.

    run_job(job, say, stop, process=None) -> the `done` payload

In a child, `process` is its Process (process.py), whose guards are up: the allocator is capped on a
card before any node code is imported, progress names the stage tqdm's patch reads, and a run that
fails leaves its peaks there for the child's `error` event. A failure goes up as it was raised; the
caller reports it.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from oneframe.node import Emit, usage
from oneframe.node.code import load_entry
from oneframe.node.context import NodeContext
from oneframe.node.process import Process


def run_job(
    job: dict[str, Any],
    say: Emit,
    stop: threading.Event | None = None,
    process: Process | None = None,
) -> dict[str, Any]:
    """Run one node and return the `done` payload. Raises on failure."""
    cap = usage.cap_allocator(job, say) if process is not None else None
    ctx = NodeContext(
        job,
        say,
        stop,
        stage=process.stage if process is not None else None,
        cap_mb=cap,
        ram_start=process.ram_start if process is not None else None,
    )
    started = time.monotonic()
    try:
        ctx.out_dir.mkdir(parents=True, exist_ok=True)
        fn = load_entry(job["entry"]["file"], job["entry"]["function"])
        stats = fn(ctx) or {}
    except BaseException:
        if process is not None:
            process.peaks = ctx.peaks()
        raise
    return {
        "event": "done",
        "outputs": ctx.outputs,
        "seconds": round(time.monotonic() - started, 3),
        **ctx.peaks(),
        "ways": dict(ctx.ways),
        "stats": stats if isinstance(stats, dict) else {},
    }
