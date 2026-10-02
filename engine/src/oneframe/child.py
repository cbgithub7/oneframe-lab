"""The far side of a node run, and the context every node is handed.

    <runtime python> -u child.py <job.json>

A heavy node runs in its own runtime -- a separate interpreter with its own packages, where
nothing of the engine is installed. This file is therefore standard library only, and is started
by path. The engine imports the same file for nodes it runs in its own process, so a node's code
sees one `NodeContext` wherever it runs.

Each of these guards against something model code does. All of them are in place before any node
code is imported:

- **The protocol gets its own descriptor.** Model code prints freely -- timers, tqdm, C extensions
  writing straight to fd 1. The NDJSON channel is a duplicate of stdout taken first; fd 1 itself
  is pointed at stderr, so nothing anybody prints can land in the middle of an event.
- **The network is closed to Python code.** The engine tells hub libraries they are offline and
  passes no hub token (executors.FORCED_ENV), so they read only what is on disk. Connecting through
  Python's sockets anywhere but this machine raises NetworkForbidden naming the host, so a run fails
  with that name instead of fetching gigabytes nobody asked for. This stops accidents, not malicious
  code: native code, DNS lookups and anything that undoes the patch go around it. Downloads happen
  when a person presses Download, never during a run.
- **tqdm reports progress.** Most model loops run through tqdm; patching its update() makes any
  loop, imported under any alias, a progress event.
- **The allocator has a ceiling.** On Windows an overrun would spill into system memory and freeze
  the desktop; with the ceiling it is an out-of-memory error the engine answers with a smaller fit.
  The ceiling is set once the CUDA context exists, from the free memory measured then, so memory
  another program took since the fit is not counted twice.

This file runs in every runtime's own Python, which may be older than the engine's: it is checked
for Python 3.11 (ruff.toml, pyrightconfig.json).
"""

from __future__ import annotations

import gc
import importlib.util
import json
import os
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, TypeVar

_OUT: Any = None
_WRITE = threading.Lock()

Emit = Callable[[dict[str, Any]], None]
T = TypeVar("T")


class NetworkForbidden(ConnectionError):
    """Something tried to download during a run.

    A ConnectionError, so an OSError: urllib wraps it in URLError, and libraries that fall back to
    their cache when offline do so here too (torch.hub asks github.com for the default branch even
    when the repo is cached, and only a URLError sends it to the cache)."""


class NodeFailed(RuntimeError):
    """A node reports a failure it understands, in words meant for the person running it."""


class Stopped(RuntimeError):
    """The run was stopped."""


def emit(payload: dict[str, Any]) -> None:
    line = json.dumps(payload, default=str, ensure_ascii=False)
    with _WRITE:
        _OUT.write(line + "\n")
        _OUT.flush()


def _take_stdout() -> None:
    global _OUT
    _OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout.flush()
    os.dup2(2, 1)
    sys.stdout = sys.stderr


_LOCAL = ("127.0.0.1", "::1", "localhost", "0.0.0.0")


def forbid_network() -> None:
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(address: Any) -> None:
        if not isinstance(address, tuple) or not address:
            return  # a Unix socket path: local by definition
        host = str(address[0])
        if host in _LOCAL or host.startswith("127."):
            return
        raise NetworkForbidden(
            f"A download was attempted during the run ({host}). Everything a node reads has to be "
            "on disk before it runs; declare it in the node's manifest so Download fetches it."
        )

    def connect(self: socket.socket, address: Any) -> Any:
        check(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> Any:
        check(address)
        return real_connect_ex(self, address)

    socket.socket.connect = connect  # type: ignore[method-assign]
    socket.socket.connect_ex = connect_ex  # type: ignore[method-assign]


def _exit_with_parent() -> None:
    """End this process when stdin closes. The parent never writes, so end of file means it has
    gone. os._exit, not sys.exit: the main thread may be inside a CUDA kernel, and the driver hands
    the card back only when the process is gone. Every run asks for it, so a node outlives neither
    an engine that was killed nor, later, a wsl.exe that was ended."""

    def watch() -> None:
        try:
            while os.read(0, 65536):
                pass
        except OSError:
            pass
        os._exit(1)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()


def _report_tqdm(say: Emit, stage: dict[str, str]) -> None:
    try:
        from tqdm import std  # type: ignore[import-not-found]
    except Exception:
        return
    real_update = std.tqdm.update
    last = {"at": 0.0}

    def update(self: Any, n: int = 1) -> Any:
        out = real_update(self, n)
        now = time.monotonic()
        total = getattr(self, "total", None)
        done = getattr(self, "n", None)
        if now - last["at"] >= 0.25 or (total and done is not None and done >= total):
            last["at"] = now
            say(
                {
                    "event": "progress",
                    "stage": stage.get("stage", ""),
                    "done": done,
                    "total": total,
                    "what": getattr(self, "desc", "") or "",
                }
            )
        return out

    std.tqdm.update = update  # pyright: ignore[reportAttributeAccessIssue]


RESERVE_MB = 256  # kept free under the cap: comfy-aimdo's default headroom (spec 002, research.md)


def _cap_allocator(job: dict[str, Any], say: Emit) -> float | None:
    """Cap torch's allocator on the card, before any node code runs, and return the cap in MB.

    The cap is the smaller of the fit's budget and the free memory measured once the CUDA context
    exists (less a small reserve), less what the node allocates outside torch. In "tried anyway" and
    with the fit off the job carries no budget, and only the free memory counts. A runtime without
    torch runs uncapped, and its `ceiling` event says so."""
    if not str(job.get("device") or "").startswith("cuda"):
        return None
    budget = job.get("vram_cap_mb")
    outside = float(job.get("outside_torch_mb") or 0)
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        why = "torch is not in this runtime, so nothing was capped"
        say({"event": "ceiling", "applied": False, "budget_mb": budget, "why": why})
        return None
    if not torch.cuda.is_available():
        why = "torch sees no card, so nothing was capped"
        say({"event": "ceiling", "applied": False, "budget_mb": budget, "why": why})
        return None
    torch.cuda.init()
    free, total = (n / 1e6 for n in torch.cuda.mem_get_info())
    cap = free - RESERVE_MB
    if budget is not None:
        cap = min(float(budget), cap)
    cap -= outside
    torch.cuda.set_per_process_memory_fraction(max(0.0, min(1.0, cap / total)))
    say(
        {
            "event": "ceiling",
            "applied": True,
            "budget_mb": None if budget is None else round(float(budget)),
            "free_mb": round(free),
            "reserve_mb": RESERVE_MB,
            "outside_torch_mb": round(outside),
            "vram_cap_mb": round(cap),
            "vram_total_mb": round(total),
        }
    )
    return cap


def peak_vram_mb(device: str, reserved: bool = False) -> float | None:
    """Torch's peak on the card, or None when torch was never loaded or the run was on the CPU
    (asking would start a CUDA context on a card the node was not using)."""
    torch = sys.modules.get("torch")
    if torch is None or not device.startswith("cuda"):
        return None
    try:
        if torch.cuda.is_available():
            peak = torch.cuda.max_memory_reserved() if reserved else torch.cuda.max_memory_allocated()
            return round(peak / 1e6, 1)
    except Exception:
        pass
    return None


def resident_mb() -> tuple[float | None, float | None]:
    """This process's resident memory now and at its peak, in MB, from the standard library:
    /proc and getrusage on Linux, GetProcessMemoryInfo on Windows. None where it cannot be read."""
    if sys.platform == "win32":
        return _windows_resident_mb()
    now: float | None = None
    peak: float | None = None
    try:
        with Path("/proc/self/statm").open(encoding="utf-8") as handle:
            now = int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        now = None
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = maxrss / 1e6 if sys.platform == "darwin" else maxrss * 1024 / 1e6  # bytes on macOS
    except (ImportError, OSError):
        peak = None
    return now, peak


def _windows_resident_mb() -> tuple[float | None, float | None]:
    if sys.platform != "win32":
        return None, None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        )

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None, None
    return counters.WorkingSetSize / 1e6, counters.PeakWorkingSetSize / 1e6


def peaks(device: str, ram_start: float | None, reserved_floor: float | None = None) -> dict[str, Any]:
    """The peaks a `done` or `error` event carries: torch's on the card (allocated and reserved,
    the reserved one never below `reserved_floor`, the peak of a ctx.fallbacks way that ran out),
    and the growth of this process's resident memory since `ram_start`."""
    reserved = peak_vram_mb(device, reserved=True)
    if reserved_floor is not None:
        reserved = max(reserved or 0.0, reserved_floor)
    _now, peak = resident_mb()
    ram = None if peak is None or ram_start is None else round(max(peak - ram_start, 0.0), 1)
    return {"peak_vram_mb": peak_vram_mb(device), "peak_reserved_mb": reserved, "peak_ram_mb": ram}


_OOM_TEXT = ("out of memory", "cublas_status_alloc_failed", "cudnn_status_alloc_failed")
_FETCH_TEXT = ("download was attempted", "OfflineModeIsEnabled", "LocalEntryNotFound")


def _chain(exc: BaseException) -> list[BaseException]:
    """The error and every error it was raised from or while handling."""
    out: list[BaseException] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        out.append(current)
        current = current.__cause__ or current.__context__
    return out


def is_oom(exc: BaseException) -> bool:
    """Out of memory in any of its common forms: torch's OutOfMemoryError, a CUDA, cuBLAS or cuDNN
    allocation failure, Python's MemoryError, or any of these as the cause of another error."""
    for error in _chain(exc):
        text = str(error).lower()
        if isinstance(error, MemoryError) or "OutOfMemory" in type(error).__name__:
            return True
        if any(needle in text for needle in _OOM_TEXT):
            return True
    return False


def is_torch_oom(exc: BaseException) -> bool:
    """torch's own OutOfMemoryError, by its name, anywhere in the chain. After it the CUDA context
    is still usable; after other CUDA memory errors it may not be, so those go to the engine."""
    return any(type(error).__name__ == "OutOfMemoryError" for error in _chain(exc))


def _release(device: str) -> None:
    """Free what a failed way left: traceback cycles can keep tensors alive until collected, and
    torch keeps freed blocks cached until asked to give them back. Then start the peak again."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None or not device.startswith("cuda"):
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass


def classify(exc: BaseException) -> str:
    """The kinds the engine acts on: oom is retried with a smaller fit; the rest stop the run."""
    if isinstance(exc, Stopped):
        return "stopped"
    for error in _chain(exc):
        text = f"{type(error).__name__}: {error}"
        if isinstance(error, NetworkForbidden) or any(needle in text for needle in _FETCH_TEXT):
            return "fetch"
    if is_oom(exc):
        return "oom"
    if isinstance(exc, ImportError):
        return "missing"
    if isinstance(exc, NodeFailed):
        return "node"
    return "error"


class Value:
    """One value on a port: its type, its facets (each with one value), a file, and notes.

    `path` is a file (or, for a ViewSet, a folder). `meta` is small JSON the node wants to pass on
    (image size, a focal length); anything large goes in the file."""

    __slots__ = ("facets", "key", "meta", "path", "trust", "type")

    def __init__(
        self,
        type: str,
        path: str | Path,
        facets: dict[str, str] | None = None,
        meta: dict[str, Any] | None = None,
        trust: str | None = None,
        key: str = "",
    ):
        self.type = type
        self.path = Path(path)
        self.facets = dict(facets or {})
        self.meta = dict(meta or {})
        self.trust = trust
        self.key = key

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": str(self.path),
            "facets": self.facets,
            "meta": self.meta,
            "trust": self.trust,
            "key": self.key,
        }

    @classmethod
    def from_json(cls, row: dict[str, Any]) -> Value:
        return cls(
            row["type"], row["path"], row.get("facets"), row.get("meta"), row.get("trust"), row.get("key", "")
        )

    def __repr__(self) -> str:
        return f"Value({self.type}, {self.path.name}, {self.facets})"


class NodeContext:
    """What a node's run function receives.

        def run(ctx):
            image = ctx.inputs["image"]            # a Value
            depth = infer(image.path, ctx.params["resolution"])
            path = ctx.path("depth.npz")          # a file in this run's output folder
            np.savez(path, depth=depth)
            ctx.output("depth", path, facets={"kind": "metric", "measure": "z"})

    Every output the manifest lists has to be declared with ctx.output before run returns."""

    def __init__(
        self,
        job: dict[str, Any],
        say: Emit,
        should_stop: Callable[[], bool] | None = None,
        stage: dict[str, str] | None = None,
        cap_mb: float | None = None,
        ram_start: float | None = None,
    ):
        self.job = job
        self.node = str(job.get("node", ""))
        self.inputs: dict[str, Value] = {k: Value.from_json(v) for k, v in (job.get("inputs") or {}).items()}
        self.params: dict[str, Any] = dict(job.get("params") or {})
        self.out_dir = Path(job["out_dir"])
        self.device = str(job.get("device") or "cpu")
        self.precision = str(job.get("precision") or "fp32")
        self.attempt = int(job.get("attempt") or 1)  # 2 on the engine's retry after running out
        # The fit's budget on this device in MB, or None when the node was not fitted.
        self.memory_budget_mb: float | None = job.get("memory_budget_mb")
        self.models = Path(job["models"]) if job.get("models") else None
        self.outputs: dict[str, dict[str, Any]] = {}
        self._say = say
        self._should_stop = should_stop or (lambda: False)
        self._stage = stage if stage is not None else {"stage": "start"}
        self._cap_mb = cap_mb
        self._ram_start = ram_start if ram_start is not None else resident_mb()[0]
        self._reserved_floor: float | None = None
        self.ways: dict[str, str] = {}  # each ctx.fallbacks step, and the way it finished with

    def memory_free_mb(self) -> float | None:
        """The memory still free for this run, in MB, or None when it is not known.

        On a card, the cap less what torch holds in live tensors; on the processor, the budget less
        what this process has grown by since it started."""
        if self.device.startswith("cuda"):
            torch = sys.modules.get("torch")
            if torch is None or self._cap_mb is None:
                return None
            return self._cap_mb - torch.cuda.memory_allocated() / 1e6
        now = resident_mb()[0]
        if self.memory_budget_mb is None or now is None or self._ram_start is None:
            return None
        return float(self.memory_budget_mb) - max(now - self._ram_start, 0.0)

    def peaks(self) -> dict[str, Any]:
        return peaks(self.device, self._ram_start, self._reserved_floor)

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
                self.ways[step] = name
                return result
            peak = peak_vram_mb(self.device, reserved=True)
            if peak is not None:
                self._reserved_floor = max(self._reserved_floor or 0.0, peak)
            _release(self.device)
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
        self.ways[step] = name
        return result

    def path(self, name: str) -> Path:
        """A path for a file this run writes. Names stay inside the output folder."""
        target = (self.out_dir / name).resolve()
        if self.out_dir.resolve() not in target.parents:
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
            target = self.out_dir / target
        self.outputs[port] = {"path": str(target), "facets": dict(facets or {}), "meta": dict(meta or {})}

    def stage(self, name: str, message: str = "") -> None:
        self._stage["stage"] = name
        self._say({"event": "stage", "stage": name, "message": message})

    def progress(self, done: float, total: float | None = None, what: str = "") -> None:
        self._say(
            {"event": "progress", "stage": self._stage["stage"], "done": done, "total": total, "what": what}
        )

    def stopped(self) -> bool:
        """For a loop that can end early: check it, and raise Stopped (or return) when true."""
        return self._should_stop()

    def check_stop(self) -> None:
        if self._should_stop():
            raise Stopped()


def load_entry(file: str | Path, function: str) -> Callable[[NodeContext], Any]:
    """Import a node's code by path under a private module name, so two nodes whose files are both
    called node.py never collide, and the node's own folder is importable for its helpers."""
    path = Path(file).resolve()
    folder = str(path.parent)
    if folder not in sys.path:
        sys.path.insert(0, folder)
    name = "oneframe_node_" + "".join(c if c.isalnum() else "_" for c in str(path))
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load node code from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    fn = getattr(module, function, None)
    if not callable(fn):
        raise NodeFailed(f"{path.name} has no function {function}().")
    return fn


def run_node(
    job: dict[str, Any],
    say: Emit,
    should_stop: Callable[[], bool] | None = None,
    stage: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one node and return the `done` payload. Raises on failure; the caller reports it."""
    return run_context(NodeContext(job, say, should_stop, stage))


def run_context(ctx: NodeContext) -> dict[str, Any]:
    started = time.monotonic()
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    fn = load_entry(ctx.job["entry"]["file"], ctx.job["entry"]["function"])
    stats = fn(ctx) or {}
    return {
        "event": "done",
        "outputs": ctx.outputs,
        "seconds": round(time.monotonic() - started, 3),
        **ctx.peaks(),
        "ways": dict(ctx.ways),
        "stats": stats if isinstance(stats, dict) else {},
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _take_stdout()
    # Its own process id: on Windows a venv's python.exe is a launcher that starts the interpreter
    # as another process, so the id the parent holds is not the one using the card.
    emit({"event": "pid", "pid": os.getpid()})
    with Path(argv[0]).open(encoding="utf-8") as handle:
        job = json.load(handle)
    if job.get("exit_with_parent"):
        _exit_with_parent()
    stage = {"stage": "start"}
    ram_start = resident_mb()[0]
    ctx: NodeContext | None = None
    try:
        if not job.get("allow_network"):
            forbid_network()
        cap = _cap_allocator(job, emit)
        _report_tqdm(emit, stage)
        ctx = NodeContext(job, emit, stage=stage, cap_mb=cap, ram_start=ram_start)
        emit(run_context(ctx))
        return 0
    except BaseException as exc:  # the engine decides what it means; report everything
        emit(
            {
                "event": "error",
                "kind": classify(exc),
                "stage": stage.get("stage"),
                "message": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc()[-4000:],
                **(ctx.peaks() if ctx else peaks(str(job.get("device") or ""), ram_start)),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
