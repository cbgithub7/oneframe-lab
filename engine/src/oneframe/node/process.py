"""The guards a runtime child puts up once, before any node code is imported (child.py).

Each guards against something model code does:

- **The protocol gets its own descriptor.** Model code prints freely -- timers, tqdm, C extensions
  writing straight to fd 1. The NDJSON channel is a duplicate of stdout taken first; fd 1 itself
  is pointed at stderr, so nothing anybody prints can land in the middle of an event. The first
  event is the interpreter's own process id.
- **It ends with its parent.** A child outlives neither an engine that was killed nor, later, a
  wsl.exe that was ended.
- **The network is closed to Python code.** The engine tells hub libraries they are offline and
  passes no hub token (environment.FORCED_ENV), so they read only what is on disk. Connecting through
  Python's sockets anywhere but this machine raises NetworkForbidden naming the host, so a run fails
  with that name instead of fetching gigabytes nobody asked for. This stops accidents, not malicious
  code: native code, DNS lookups and anything that undoes the patch go around it. Downloads happen
  when a person presses Download, never during a run.
- **tqdm reports progress.** Most model loops run through tqdm; patching its update() makes any
  loop, imported under any alias, a progress event.

The ceiling on torch's allocator is the runner's, once per job (usage.cap_allocator). None of this
is ever done in the engine's process: it would close the engine's own downloads.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import Any

from oneframe.node import Emit, usage
from oneframe.node.failures import NetworkForbidden

_OUT: Any = None
_WRITE = threading.Lock()
_LOCAL = ("127.0.0.1", "::1", "localhost", "0.0.0.0")


class Process:
    """This interpreter, once its guards are up: the stage tqdm's progress names, the resident
    memory before any node code ran, and, after a run that failed, the peaks it had reached
    (runner.run_job), for the child's `error` event."""

    def __init__(self) -> None:
        self.stage: dict[str, str] = {"stage": "start"}
        self.ram_start = usage.resident_mb()[0]
        self.peaks: dict[str, Any] | None = None


def emit(payload: dict[str, Any]) -> None:
    """One event on the protocol's own descriptor."""
    line = json.dumps(payload, default=str, ensure_ascii=False)
    with _WRITE:
        _OUT.write(line + "\n")
        _OUT.flush()


def open_protocol() -> None:
    """Take stdout for the protocol, and say this interpreter's process id: on Windows a venv's
    python.exe is a launcher that starts the interpreter as another process, so the id the parent
    holds is not the one using the card."""
    global _OUT
    _OUT = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout.flush()
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    emit({"event": "pid", "pid": os.getpid()})


def exit_with_parent() -> None:
    """End this process when stdin closes. The parent never writes, so end of file means it has
    gone. os._exit, not sys.exit: the main thread may be inside a CUDA kernel, and the driver hands
    the card back only when the process is gone."""

    # The watcher reads its own copy of the pipe; node code gets an empty stdin, so input() or a
    # library's prompt ends at once with EOFError instead of waiting on a pipe nobody writes to.
    watched = os.dup(0)
    null = os.open(os.devnull, os.O_RDONLY)
    os.dup2(null, 0)
    os.close(null)
    sys.stdin = Path(os.devnull).open(encoding="utf-8")  # noqa: SIM115

    def watch() -> None:
        try:
            while os.read(watched, 65536):
                pass
        except OSError:
            pass
        os._exit(1)

    threading.Thread(target=watch, name="exit-with-parent", daemon=True).start()


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


def report_tqdm(say: Emit, stage: dict[str, str]) -> None:
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
