"""What runs one job, the same in the engine's process and in a runtime child.

    run_job(job, say, stop, process=None) -> Done

For a node whose manifest names `run.load`, each attempt calls `state = load(view)` once, where the
view is the context without the run's inputs, paths or outputs, then `run(ctx, state)`; otherwise
`run(ctx)`. The run's seconds span both.

In a child, `process` is its Process (process.py), whose guards are up: the allocator is capped on a
card before any node code is imported, progress names the stage tqdm's patch reads, and a run that
fails leaves its peaks there for the child's `error` event. A failure goes up as it was raised; the
caller reports it.
"""

from __future__ import annotations

import threading
import time

from oneframe.node import Emit, usage
from oneframe.node.code import load_code
from oneframe.node.context import NodeContext
from oneframe.node.job import Done, Job
from oneframe.node.process import Process


def run_job(job: Job, say: Emit, stop: threading.Event | None = None, process: Process | None = None) -> Done:
    """Run one node and return what it gave. Raises on failure."""
    cap = None
    if process is not None:
        cap = usage.cap_allocator(job.device, job.vram_cap_mb, job.outside_torch_mb, say)
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
        job.out_dir.mkdir(parents=True, exist_ok=True)
        run, load = load_code(job.node, job.folder, job.entry, job.load)
        stats = run(ctx) if load is None else run(ctx, load(ctx._view()))
    except BaseException:
        if process is not None:
            process.peaks = ctx._peaks()
        raise
    return ctx._done(round(time.monotonic() - started, 3), stats)
