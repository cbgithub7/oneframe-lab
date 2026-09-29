"""The far side of a node run, and the context every node is handed.

    <runtime python> -u child.py <job.json>

A heavy node runs in its own runtime -- a separate interpreter with its own packages, where
nothing of the engine is installed. This file is therefore standard library only, and is started
by path. The engine imports the same file for nodes it runs in its own process, so a node's code
sees one `NodeContext` wherever it runs.

Carried over from the depth-pro-gui object generators, where each of these was learned the hard
way. Before any node code is imported:

- **The protocol gets its own descriptor.** Model code prints freely -- timers, tqdm, C extensions
  writing straight to fd 1. The NDJSON channel is a duplicate of stdout taken first; fd 1 itself
  is pointed at stderr, so nothing anybody prints can land in the middle of an event.
- **The network is closed.** Hub libraries are also told they are offline, but only the ones that
  read those flags listen. Connecting anywhere but this machine raises NetworkForbidden naming the
  host, so a run fails with that name instead of fetching gigabytes nobody asked for. Downloads
  happen when a person presses Download, never during a run.
- **tqdm reports progress.** Most model loops run through tqdm; patching its update() makes any
  loop, imported under any alias, a progress event.
- **The allocator has a ceiling.** On Windows an overrun would spill into system memory and freeze
  the desktop; with the ceiling it is an out-of-memory error the engine answers by trying the
  node's next arrangement.
"""

from __future__ import annotations

import importlib.util
import json
import os
import socket
import sys
import threading
import time
import traceback
from collections.abc import Callable
from pathlib import Path
from typing import Any

_OUT: Any = None
_WRITE = threading.Lock()

Emit = Callable[[dict[str, Any]], None]


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
    the card back only when the process is gone. Used for runtimes inside WSL, where ending
    wsl.exe would leave the Linux process running."""

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


def _cap_allocator(job: dict[str, Any], say: Emit) -> None:
    cap = float(job.get("vram_cap_mb") or 0)
    if cap <= 0 or not str(job.get("device") or "").startswith("cuda"):
        return
    import torch  # type: ignore[import-not-found]

    if not torch.cuda.is_available():
        return
    total = torch.cuda.get_device_properties(0).total_memory / 1e6
    torch.cuda.set_per_process_memory_fraction(max(0.01, min(1.0, cap / total)))
    say({"event": "ceiling", "vram_cap_mb": round(cap), "vram_total_mb": round(total)})


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


def classify(exc: BaseException) -> str:
    """The kinds the engine acts on: oom moves down the ladder; the rest stop the run."""
    text = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, Stopped):
        return "stopped"
    if (
        isinstance(exc, NetworkForbidden)
        or "download was attempted" in text
        or "OfflineModeIsEnabled" in text
        or "LocalEntryNotFound" in text
    ):
        return "fetch"
    if "OutOfMemory" in type(exc).__name__ or "out of memory" in str(exc).lower():
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
    ):
        self.job = job
        self.node = str(job.get("node", ""))
        self.inputs: dict[str, Value] = {k: Value.from_json(v) for k, v in (job.get("inputs") or {}).items()}
        self.params: dict[str, Any] = dict(job.get("params") or {})
        self.out_dir = Path(job["out_dir"])
        self.device = str(job.get("device") or "cpu")
        self.precision = str(job.get("precision") or "fp32")
        self.models = Path(job["models"]) if job.get("models") else None
        self.outputs: dict[str, dict[str, Any]] = {}
        self._say = say
        self._should_stop = should_stop or (lambda: False)
        self._stage = stage if stage is not None else {"stage": "start"}

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
    started = time.monotonic()
    ctx = NodeContext(job, say, should_stop, stage)
    ctx.out_dir.mkdir(parents=True, exist_ok=True)
    fn = load_entry(job["entry"]["file"], job["entry"]["function"])
    stats = fn(ctx) or {}
    return {
        "event": "done",
        "outputs": ctx.outputs,
        "seconds": round(time.monotonic() - started, 3),
        "peak_vram_mb": peak_vram_mb(ctx.device),
        "peak_reserved_mb": peak_vram_mb(ctx.device, reserved=True),
        "stats": stats if isinstance(stats, dict) else {},
    }


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _take_stdout()
    with Path(argv[0]).open(encoding="utf-8") as handle:
        job = json.load(handle)
    if job.get("exit_with_parent"):
        _exit_with_parent()
    stage = {"stage": "start"}
    try:
        if not job.get("allow_network"):
            forbid_network()
        _cap_allocator(job, emit)
        _report_tqdm(emit, stage)
        emit(run_node(job, emit, stage=stage))
        return 0
    except BaseException as exc:  # the engine decides what it means; report everything
        emit(
            {
                "event": "error",
                "kind": classify(exc),
                "stage": stage.get("stage"),
                "message": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc()[-4000:],
                "peak_vram_mb": peak_vram_mb(str(job.get("device") or "")),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
