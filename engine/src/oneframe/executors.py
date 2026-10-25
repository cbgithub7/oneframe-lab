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
from oneframe.environment import child_env
from oneframe.errors import KINDS, Failure, Stopped

LOGGER = logging.getLogger(__name__)
CHILD = Path(child.__file__).resolve()


def child_command(python: Path, job: Path) -> list[str]:
    """How a runtime's Python runs a node: -P keeps child.py's folder, the engine's own modules, off
    the node's import path; -u keeps the protocol unbuffered."""
    return [str(python), "-P", "-u", str(CHILD), str(job)]


NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

Emit = Callable[[dict[str, Any]], None]
ShouldStop = Callable[[], bool]


class NodeError(Failure):
    """A node ended without its outputs. `kind` is a declared kind (errors.py): oom, memory,
    fetch, missing, node, error, died or contract. `peaks` are what the run had used when it ended
    (`peak_reserved_mb`, `peak_vram_mb`, `peak_ram_mb`), so a failed attempt can be reported and
    learned from."""

    def __init__(
        self,
        kind: str,
        message: str,
        detail: str = "",
        peaks: dict[str, Any] | None = None,
        next: str | None = None,
    ):
        super().__init__(message, next=next, kind=kind)
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


class EngineExecutor:
    """Runs a node in the engine's process. Stop is cooperative: the node checks ctx.stopped()."""

    def execute(self, job: dict[str, Any], emit: Emit, should_stop: ShouldStop) -> dict[str, Any]:
        try:
            return child.run_node(job, emit, should_stop)
        except Stopped:
            raise
        except Exception as exc:
            kind = declared_kind(child.classify(exc))
            raise NodeError(kind, child.describe(exc), _trace()) from exc


def declared_kind(kind: str) -> str:
    """A child's word for its failure, as a declared kind: one errors.py does not know (a newer
    child, or a node writing on the protocol stream) is reported as `error`, never lost."""
    return kind if kind in KINDS else "error"


def _trace() -> str:
    import traceback

    return traceback.format_exc()[-4000:]


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
        tmp_root: Path | None = None,
        caches: Path | None = None,
    ):
        self.python = Path(python)
        self.caches = caches  # the runtime's folder for library caches, under the data root
        # Where each job's own folder is made: the process's folder under cache/tmp/, or, when not
        # given, the process's temporary folder (which an engine or bench tool points there too).
        self.tmp_root = tmp_root
        self.env = dict(env or {})
        self.base_env = base_env
        self.poll = poll
        self.log_dir = log_dir
        self.on_pid = on_pid  # told each child's own process id, for a bench that watches the process

    def execute(self, job: dict[str, Any], emit: Emit, should_stop: ShouldStop) -> dict[str, Any]:
        # Every child ends when the process that started it does, even one killed without a chance
        # to stop it (an engine, a bench tool), so no orphan keeps holding the card. It watches
        # the stdin pipe this process holds, and closes.
        job = {**job, "exit_with_parent": True}
        tmp = Path(tempfile.mkdtemp(prefix="job-", dir=self.tmp_root))
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
                child_command(self.python, job_path),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=child_env(
                    self.base_env, {**self.env, **(job.get("env") or {})}, caches=self.caches, tmp=tmp
                ),
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
                    try:
                        proc.wait(timeout=10)
                    except subprocess.TimeoutExpired:  # never hide why the run ended behind this
                        LOGGER.warning("node child %s did not end within 10 s of being killed", proc.pid)
                if proc.stdin is not None:
                    # The child's own interpreter, behind a launcher that a kill does not take down
                    # with it, sees its stdin close and ends.
                    proc.stdin.close()
            if done is not None:
                return done
            if failed is not None and failed.get("kind") == "stopped":
                raise Stopped()  # the node stopped itself, as ctx.stopped() told it to
            if failed is not None:
                raise NodeError(
                    declared_kind(str(failed.get("kind") or "error")),
                    str(failed.get("message")),
                    str(failed.get("trace") or ""),
                    {k: failed.get(k) for k in PEAKS},
                )
            raise NodeError("died", died_message(proc.returncode), "\n".join(stderr_tail))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
