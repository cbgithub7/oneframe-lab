"""Where a node's code runs: in the engine's own process, or in a child in the node's runtime.

Both take the same job and give the same `done` payload, so the scheduler does not care which.
The engine process runs only light nodes (sources, converters, evaluators) whose code ships with
the app. Anything that imports a model library runs in a child: a different interpreter with its
own packages, started per node run and killed outright on Stop -- a model holds the card while it
runs, and nothing it is doing is worth finishing once someone has asked it to stop.

The child speaks NDJSON on a private copy of its stdout (see child.py); the job goes in as a JSON
file. Pickle cannot cross between interpreters with different packages, and JSON can.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe import child

LOGGER = logging.getLogger(__name__)
CHILD = Path(child.__file__).resolve()
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Emit = Callable[[dict[str, Any]], None]
ShouldStop = Callable[[], bool]


class NodeError(RuntimeError):
    """A node ended without its outputs. `kind` is oom / fetch / missing / node / error / died.
    `peaks` are what the run had used when it ended (`peak_reserved_mb`, `peak_vram_mb`,
    `peak_ram_mb`), so a failed attempt can be reported and learned from."""

    def __init__(self, kind: str, message: str, detail: str = "", peaks: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail
        self.peaks = dict(peaks or {})


PEAKS = ("peak_reserved_mb", "peak_vram_mb", "peak_ram_mb")
# How Windows reports a process ended for lack of memory, as Python gives the exit code (unsigned):
# STATUS_NO_MEMORY, and STATUS_COMMITMENT_LIMIT (the page file is full).
WINDOWS_NO_MEMORY = (0xC0000017, 0xC000012D)


def died_message(code: int | None, platform: str = sys.platform) -> str:
    """What a child that ended without a word is reported as. On Linux -9 is how the kernel's
    out-of-memory killer ends a process (though anything may send it); on Windows the codes say so."""
    text = f"The node's process exited with code {code} and said nothing."
    if (platform == "linux" and code == -9) or (platform == "win32" and code in WINDOWS_NO_MEMORY):
        text += " It may have run out of system memory."
    return text


class Stopped(RuntimeError):
    """Stop was pressed."""


class EngineExecutor:
    """Runs a node in the engine's process. Stop is cooperative: the node checks ctx.stopped()."""

    def execute(self, job: dict[str, Any], emit: Emit, should_stop: ShouldStop) -> dict[str, Any]:
        try:
            return child.run_node(job, emit, should_stop)
        except child.Stopped as exc:
            raise Stopped() from exc
        except Exception as exc:
            kind = child.classify(exc)
            raise NodeError(kind, f"{type(exc).__name__}: {exc}", _trace()) from exc


def _trace() -> str:
    import traceback

    return traceback.format_exc()[-4000:]


# Set in every runtime child, after everything else, so neither the person's environment nor a
# runtime's definition can turn them off. Hub libraries told they are offline read only what is on
# disk and fail at once on anything missing, instead of retrying for seconds against a closed
# network. torch then loads every checkpoint with weights_only, whatever the caller asks for.
FORCED_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
}
# Never passed to a runtime child: a run reads only files already on disk, so it needs no hub
# token or endpoint, and nothing may turn torch's weights_only loading back off.
SECRET_OR_UNSAFE_ENV = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HF_TOKEN_PATH",
    "HF_ENDPOINT",
    "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD",
)


def child_env(base: dict[str, str] | None, extra: dict[str, str]) -> dict[str, str]:
    """A runtime child's environment: nothing of the engine's own Python leaks in, so the runtime
    never sees the engine's packages or loads a DLL from the engine's folders; no hub token or
    endpoint reaches it; and FORCED_ENV is set last."""
    source = dict(os.environ if base is None else base)
    drop = (
        "PYTHONPATH",
        "PYTHONHOME",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "CONDA_DEFAULT_ENV",
        "UV_PROJECT_ENVIRONMENT",
        *SECRET_OR_UNSAFE_ENV,
    )
    env = {k: v for k, v in source.items() if k not in drop}
    engine_prefix = os.path.normcase(str(Path(sys.prefix).resolve()))
    if env.get("PATH"):
        env["PATH"] = os.pathsep.join(
            part
            for part in env["PATH"].split(os.pathsep)
            if part and not os.path.normcase(str(Path(part).resolve())).startswith(engine_prefix)
        )
    env.update({k: v for k, v in extra.items() if k not in SECRET_OR_UNSAFE_ENV})
    env.update(FORCED_ENV)
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _reader(stream: Any, sink: queue.Queue[str | None]) -> None:
    try:
        for line in iter(stream.readline, ""):
            sink.put(line)
    finally:
        sink.put(None)


def _tail(stream: Any, keep: deque[str], log: Path | None) -> None:
    handle = log.open("a", encoding="utf-8") if log else None
    try:
        for line in iter(stream.readline, ""):
            keep.append(line.rstrip())
            if handle:
                handle.write(line)
                handle.flush()
    finally:
        if handle:
            handle.close()


class ProcessExecutor:
    """Runs a node in a child process with a given interpreter."""

    def __init__(
        self,
        python: Path,
        env: dict[str, str] | None = None,
        base_env: dict[str, str] | None = None,
        poll: float = 0.2,
        log_dir: Path | None = None,
        on_pid: Callable[[int], None] | None = None,
    ):
        self.python = Path(python)
        self.env = dict(env or {})
        self.base_env = base_env
        self.poll = poll
        self.log_dir = log_dir
        self.on_pid = on_pid  # told each child's own process id, for a bench that watches the process

    def execute(self, job: dict[str, Any], emit: Emit, should_stop: ShouldStop) -> dict[str, Any]:
        tmp = Path(tempfile.mkdtemp(prefix="oneframe-job-"))
        stderr_tail: deque[str] = deque(maxlen=80)
        done: dict[str, Any] | None = None
        failed: dict[str, Any] | None = None
        log = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            log = self.log_dir / f"{job.get('run', 'run')}-{job.get('step', 'node')}.log"
        try:
            job_path = tmp / "job.json"
            job_path.write_text(json.dumps(job, default=str), encoding="utf-8")
            proc = subprocess.Popen(
                [str(self.python), "-u", str(CHILD), str(job_path)],
                stdin=subprocess.PIPE if job.get("exit_with_parent") else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env(self.base_env, {**self.env, **(job.get("env") or {})}),
                cwd=tmp,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=NO_WINDOW,
            )
            LOGGER.info("started node child %s for %s", proc.pid, job.get("node"))
            lines: queue.Queue[str | None] = queue.Queue()
            threading.Thread(target=_reader, args=(proc.stdout, lines), daemon=True).start()
            threading.Thread(target=_tail, args=(proc.stderr, stderr_tail, log), daemon=True).start()
            try:
                open_stream = True
                while open_stream or proc.poll() is None:
                    if should_stop():
                        raise Stopped()
                    try:
                        line = lines.get(timeout=self.poll)
                    except queue.Empty:
                        continue
                    if line is None:
                        open_stream = False
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        LOGGER.warning("node child wrote a non-event line: %s", line.rstrip()[:200])
                        continue
                    kind = event.get("event")
                    if kind == "pid":
                        if self.on_pid is not None:  # the interpreter's own id (see child.main)
                            self.on_pid(int(event["pid"]))
                    elif kind == "done":
                        done = event
                    elif kind == "error":
                        failed = event
                    else:
                        emit(event)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=10)
            if done is not None:
                return done
            if failed is not None:
                raise NodeError(
                    str(failed.get("kind") or "error"),
                    str(failed.get("message")),
                    str(failed.get("trace") or ""),
                    {k: failed.get(k) for k in PEAKS},
                )
            raise NodeError("died", died_message(proc.returncode), "\n".join(stderr_tail))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
