"""The context a node's run function is handed, in the engine's process or in a runtime child.

Its public members are exactly its Protocol's (`oneframe.node.Context`); what the runner reads --
the outputs, the ways, the peaks, the `Done` they make, and the view `load` is handed -- is
private."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn, TypeVar

from oneframe.node import Context, Emit, LoadContext, NodeFailed, Stopped, Value, usage
from oneframe.node.failures import is_torch_oom
from oneframe.node.job import Done, Job

T = TypeVar("T")


class NodeContext(Context):
    """What a node's run function receives.

        def run(ctx: Context):
            image = ctx.inputs["image"]            # a Value
            depth = infer(image.path, ctx.params["resolution"])
            path = ctx.path("depth.npz")          # a file in this run's output folder
            np.savez(path, depth=depth)
            ctx.output("depth", path, facets={"kind": "metric", "measure": "z"})

    Every output the manifest lists has to be declared with ctx.output before run returns."""

    def __init__(
        self,
        job: Job,
        say: Emit,
        stop: threading.Event | None = None,
        stage: dict[str, str] | None = None,
        cap_mb: float | None = None,
        ram_start: float | None = None,
    ):
        self.inputs: dict[str, Value] = dict(job.inputs)
        self.params: dict[str, Any] = dict(job.params)
        self._out_dir = job.out_dir
        self.device = job.device or "cpu"
        self.precision = job.precision or "fp32"
        self.attempt = job.attempt  # 2 on the engine's retry after running out
        # The fit's budget on this device in MB, or None when the node was not fitted.
        self.memory_budget_mb: float | None = job.memory_budget_mb
        # The files this run reads (spec 003), by their name in the manifest: ctx.file(name).
        self._files: dict[str, str] = dict(job.files)
        self._outputs: dict[str, dict[str, Any]] = {}
        self._say = say
        self._stop = stop if stop is not None else threading.Event()
        self._stage = stage if stage is not None else {"stage": "start"}
        self._cap_mb = cap_mb
        self._ram_start = ram_start if ram_start is not None else usage.resident_mb()[0]
        self._reserved_floor: float | None = None
        self._ways: dict[str, str] = {}  # each ctx.fallbacks step, and the way it finished with

    def memory_free_mb(self) -> float | None:
        """The memory still free for this run, in MB, or None when it is not known.

        On a card, the cap less what torch holds in live tensors; on the processor, the budget less
        what this process has grown by since it started."""
        if self.device.startswith("cuda"):
            torch = sys.modules.get("torch")
            if torch is None or self._cap_mb is None:
                return None
            return self._cap_mb - torch.cuda.memory_allocated() / 1e6
        now = usage.resident_mb()[0]
        if self.memory_budget_mb is None or now is None or self._ram_start is None:
            return None
        return float(self.memory_budget_mb) - max(now - self._ram_start, 0.0)

    def _peaks(self) -> dict[str, Any]:
        return usage.peaks(self.device, self._ram_start, self._reserved_floor)

    def _view(self) -> LoadContext:
        """What `load` is handed: this context without the run's inputs, paths or outputs."""
        return _LoadView(self)

    def _done(self, seconds: float, stats: Any) -> Done:
        """What the run gave, once its function has returned `stats`."""
        return Done(
            outputs=dict(self._outputs),
            seconds=seconds,
            peaks=self._peaks(),
            ways=dict(self._ways),
            stats=stats if isinstance(stats, dict) else {},
        )

    def fallbacks(self, step: str, ways: Sequence[tuple[str, Callable[[], T]]]) -> T:
        """Run one step of the node, trying each way in turn when torch runs out of memory.

            mesh = ctx.fallbacks("decode", [
                ("whole", lambda: decode(latents)),
                ("tiled", lambda: decode_tiled(latents, tile=256)),
            ])

        Every way must give the same result within rounding: a way that costs quality belongs in
        the memory model's changes, where the fit can weigh it. Only torch's OutOfMemoryError moves
        on to the next way; anything else, and the last way running out, goes up as it is. Before
        the next way the failed one is released (its exception and frames dropped, collected, and
        torch's cache emptied), so it does not hold the memory the next way needs."""
        if not ways:
            raise ValueError("ctx.fallbacks needs at least one way")
        for index, (name, fn) in enumerate(ways[:-1]):
            try:
                result = fn()
            except BaseException as exc:
                if not is_torch_oom(exc):
                    raise
                # It ran out. Only its text outlives this block, so the failed way's frames can go.
                message = str(exc)
            else:
                self._ways[step] = name
                return result
            peak = usage.peak_vram_mb(self.device, reserved=True)
            if peak is not None:
                self._reserved_floor = max(self._reserved_floor or 0.0, peak)
            usage.release(self.device)
            self._say(
                {
                    "event": "step_oom",
                    "stage": step,
                    "way": name,
                    "next": ways[index + 1][0],
                    "peak_reserved_mb": peak,
                    "message": message,
                }
            )
        name, fn = ways[-1]
        result = fn()
        self._ways[step] = name
        return result

    def file(self, name: str) -> Path:
        """A file this node reads, by its name in the manifest's `files`: the store's copy, checked
        before the run started. Only this run's own files are here: the shared ones, and those of
        the checkpoint it runs at. The file is read-only; never write to it."""
        found = self._files.get(name)
        if found is None:
            known = ", ".join(sorted(self._files)) or "none"
            raise NodeFailed(f"{name!r} is not a file this run reads (it reads: {known}).")
        return Path(found)

    def path(self, name: str) -> Path:
        """A path for a file this run writes. Names stay inside the output folder."""
        target = (self._out_dir / name).resolve()
        if self._out_dir.resolve() not in target.parents:
            raise NodeFailed(f"{name!r} is outside this run's output folder.")
        target.parent.mkdir(parents=True, exist_ok=True)
        return target

    def output(
        self,
        port: str,
        path: str | Path,
        facets: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
    ) -> None:
        target = Path(path)
        if not target.is_absolute():
            target = self._out_dir / target
        self._outputs[port] = {"path": str(target), "facets": dict(facets or {}), "meta": dict(meta or {})}

    def stage(self, name: str, message: str = "") -> None:
        self._stage["stage"] = name
        self._say({"event": "stage", "stage": name, "message": message})

    def progress(self, done: float, total: float | None = None, what: str = "") -> None:
        self._say(
            {"event": "progress", "stage": self._stage["stage"], "done": done, "total": total, "what": what}
        )

    def check_stop(self) -> None:
        """Raises Stopped when Stop was pressed. A node in a runtime never sees it: Stop ends its
        process at once. A node in the engine's process is stopped only by this."""
        if self._stop.is_set():
            raise Stopped()

    def fail(self, message: str) -> NoReturn:
        """Ends the run with kind `node` and this message, shown to the person as it is written. Called
        while handling an out-of-memory error (or a download), the run keeps that kind, so the engine
        can answer it; the message's own words never change the kind."""
        raise NodeFailed(message)


class _LoadView(LoadContext):
    """A run's context as `load` sees it. Everything it does is the run's own: a stage, a way
    ctx.fallbacks took, a peak."""

    def __init__(self, ctx: NodeContext):
        self.params = dict(ctx.params)
        self.device = ctx.device
        self.precision = ctx.precision
        self.attempt = ctx.attempt
        self.memory_budget_mb = ctx.memory_budget_mb
        self._ctx = ctx

    def memory_free_mb(self) -> float | None:
        return self._ctx.memory_free_mb()

    def fallbacks(self, step: str, ways: Sequence[tuple[str, Callable[[], T]]]) -> T:
        return self._ctx.fallbacks(step, ways)

    def file(self, name: str) -> Path:
        return self._ctx.file(name)

    def stage(self, name: str, message: str = "") -> None:
        self._ctx.stage(name, message)

    def progress(self, done: float, total: float | None = None, what: str = "") -> None:
        self._ctx.progress(done, total, what)

    def check_stop(self) -> None:
        self._ctx.check_stop()

    def fail(self, message: str) -> NoReturn:
        self._ctx.fail(message)
