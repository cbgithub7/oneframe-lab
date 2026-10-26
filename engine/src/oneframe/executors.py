"""Where a node's code runs: in the engine's own process, or in a child in the node's runtime.

Both are an `Executor`: they take the same `Job` and give the same `Done` (oneframe/node/job.py), so
the scheduler does not care which. It asks its `Executors` for one at each attempt; the engine's is
`OneProcessPerRun`, and spec 004's worker pool is another. `run_probe` runs a runtime's own code, for
the benches.
The engine process runs only light nodes (sources, converters, evaluators) whose code ships with
the app. Anything that imports a model library runs in a child: a different interpreter with its
own packages, started per node run and, on Stop, ended outright with every process it started -- a
model holds the card while it runs, and nothing it is doing is worth finishing once someone has
asked it to stop.

The child speaks NDJSON on a private copy of its stdout (see oneframe/node/process.py); the job goes
in as a JSON file, and the result comes back as its `done` event. Pickle cannot cross between
interpreters with different packages, and JSON can.
"""

from __future__ import annotations

import json
import logging
import sys
import tempfile
import threading
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from oneframe.environment import child_env
from oneframe.errors import KINDS, Failure
from oneframe.files import remove_tree
from oneframe.journal import prune_step_logs
from oneframe.node import Emit, Stopped, failures
from oneframe.node.job import Done, Job
from oneframe.node.runner import run_job
from oneframe.node.usage import PEAKS
from oneframe.processes import Child

if TYPE_CHECKING:
    from oneframe.manifest import Manifest
    from oneframe.runtimes.manager import RuntimeSide

LOGGER = logging.getLogger(__name__)
CHILD = Path(__file__).resolve().parent / "child.py"  # started by path, never imported


def child_command(python: Path, job: Path, exit_with_parent: bool = False) -> list[str]:
    """How a runtime's Python runs a node: -P keeps child.py's folder, the engine's own modules, off
    the node's import path; -u keeps the protocol unbuffered. With `exit_with_parent` the child ends
    when its stdin closes, so it needs a stdin pipe the parent holds."""
    flag = ["--exit-with-parent"] if exit_with_parent else []
    return [str(python), "-P", "-u", str(CHILD), *flag, str(job)]


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
    """Runs a node in the engine's process. Stop is cooperative: the node calls ctx.check_stop()."""

    def execute(self, job: Job, emit: Emit, stop: threading.Event) -> Done:
        try:
            return run_job(job, emit, stop)
        except Stopped:
            raise
        except Exception as exc:
            kind = declared_kind(failures.classify(exc))
            raise NodeError(kind, failures.describe(exc), _trace()) from exc


def declared_kind(kind: str) -> str:
    """A child's word for its failure, as a declared kind: one errors.py does not know (a newer
    child, or a node writing on the protocol stream) is reported as `error`, never lost."""
    return kind if kind in KINDS else "error"


def _trace() -> str:
    import traceback

    return traceback.format_exc()[-4000:]


class _Tail:
    """A child's stderr, line by line: the last lines kept for a failure's report, every one in the
    step's log. A log that cannot be opened costs the log, never the run."""

    def __init__(self, log: Path | None):
        self.lines: deque[str] = deque(maxlen=80)
        self._handle = None
        if log is not None:
            try:
                self._handle = log.open("a", encoding="utf-8")
            except OSError as exc:
                LOGGER.warning("could not open the step log %s: %s", log, exc)

    def __call__(self, line: str) -> None:
        self.lines.append(line.rstrip())
        if self._handle is not None:
            self._handle.write(line + "\n")
            self._handle.flush()

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def _remove(folder: Path) -> None:
    """A job's folder; one that will not go (a file something still holds) is left to the next
    sweep, never hiding how the run ended."""
    try:
        remove_tree(folder)
    except OSError as exc:
        LOGGER.warning("could not remove the job folder %s: %s", folder, exc)


class ProcessExecutor:
    """Runs a node in a child process with a given interpreter."""

    def __init__(
        self,
        python: Path,
        env: dict[str, str] | None = None,
        base_env: dict[str, str] | None = None,
        log_dir: Path | None = None,
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
        self.log_dir = log_dir

    def execute(self, job: Job, emit: Emit, stop: threading.Event) -> Done:
        tmp = Path(tempfile.mkdtemp(prefix="job-", dir=self.tmp_root))
        done: Done | None = None
        failed: dict[str, Any] | None = None
        log = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            prune_step_logs(self.log_dir)
            log = self.log_dir / f"{job.run or 'run'}-{job.step or 'node'}.log"
        tail = _Tail(log)
        try:
            job_path = tmp / "job.json"
            job_path.write_text(job.to_json(), encoding="utf-8")
            # Every child ends when the process that started it does, even one killed without a
            # chance to stop it (an engine, a bench tool), so no orphan keeps holding the card. It
            # watches the stdin pipe this process holds, and closes.
            proc = Child.start(
                child_command(self.python, job_path, exit_with_parent=True),
                env=child_env(self.base_env, self.env, caches=self.caches, tmp=tmp),
                cwd=tmp,
                stdin="pipe",
                stderr=tail,
            )
            LOGGER.info("started node child %s for %s", proc.pid, job.node)
            # The interpreter's own id, which on Windows is not the process started: a venv's
            # python.exe is a launcher. A Stop returns only once it is gone (see child.main).
            interpreter: list[int] = []
            try:
                for line in proc.lines(stop):
                    try:
                        event = json.loads(line)
                    except ValueError:
                        LOGGER.warning("node child wrote a non-event line: %s", line[:200])
                        continue
                    kind = event.get("event")
                    if kind == "pid":
                        interpreter.append(int(event["pid"]))
                    elif kind == "done":
                        done = Done.from_event(event)
                    elif kind == "error":
                        failed = event
                    else:
                        emit(event)
            finally:
                # Everything the node started ends with it, and its interpreter, behind a launcher a
                # kill may not take down with it, also sees its stdin close and ends. Once the child
                # has ended, so has the interpreter, whose id may already be another process's.
                proc.kill(also=interpreter if proc.returncode is None else ())
            if done is not None:
                return done
            if failed is not None and failed.get("kind") == "stopped":
                raise Stopped()  # the node let ctx.check_stop()'s Stopped go up
            if failed is not None:
                raise NodeError(
                    declared_kind(str(failed.get("kind") or "error")),
                    str(failed.get("message")),
                    str(failed.get("trace") or ""),
                    {k: failed.get(k) for k in PEAKS},
                )
            raise NodeError("died", died_message(proc.returncode), "\n".join(tail.lines))
        finally:
            tail.close()
            _remove(tmp)


class Executor(Protocol):
    """Runs one job and gives its result, or raises NodeError or Stopped."""

    def execute(self, job: Job, emit: Emit, stop: threading.Event) -> Done: ...


class Executors(Protocol):
    """Where a node runs, asked for each attempt, so a retry after `oom` can get a new process.
    Raises RuntimeMissing when the node's runtime cannot run here."""

    def executor(self, manifest: Manifest, device: str) -> Executor: ...


class OneProcessPerRun:
    """An engine node in the engine's own process; a runtime node in a new child of its runtime for
    each attempt, from the runtime's interpreter, variables and cache folder. A child on the
    processor sees no card: the process that runs a job hides them, not the job."""

    def __init__(self, runtimes: RuntimeSide, *, tmp_root: Path | None = None, log_dir: Path | None = None):
        self.runtimes = runtimes
        self.tmp_root = tmp_root  # where each job's own folder is made
        self.log_dir = log_dir  # where each step's log is kept
        self.engine = EngineExecutor()

    def executor(self, manifest: Manifest, device: str) -> Executor:
        if manifest.run.where == "engine":
            return self.engine
        hidden = {"CUDA_VISIBLE_DEVICES": "-1"} if device == "cpu" else {}
        return _in_runtime(self.runtimes, str(manifest.run.runtime), hidden, self.tmp_root, self.log_dir)


def run_probe(
    runtimes: RuntimeSide,
    runtime_id: str,
    folder: Path,
    entry: str,
    *,
    device: str,
    params: dict[str, Any] | None = None,
    log_dir: Path | None = None,
) -> Done:
    """Run a runtime's own code (its probe, or a bench's) as a job in a child of that runtime, and
    give its `Done`. It never hides the cards, even on the processor: a probe that measures a card
    starts CUDA itself, uncapped."""
    with tempfile.TemporaryDirectory(prefix="oneframe-probe-") as out:
        job = Job(
            node=f"probe:{runtime_id}",
            folder=folder,
            entry=entry,
            out_dir=Path(out),
            run="bench",
            step="probe",
            params=dict(params or {}),
            device=device,
        )
        executor = _in_runtime(runtimes, runtime_id, {}, None, log_dir)
        return executor.execute(job, lambda _e: None, threading.Event())


def _in_runtime(
    runtimes: RuntimeSide, runtime_id: str, env: dict[str, str], tmp_root: Path | None, log_dir: Path | None
) -> ProcessExecutor:
    """A child of a runtime, with `env` over the runtime's own variables. The interpreter is asked
    for first, so a runtime that cannot run here says why before anything else is read."""
    python = runtimes.python_for(runtime_id)
    return ProcessExecutor(
        python,
        env={**runtimes.env_for(runtime_id), **env},
        log_dir=log_dir,
        tmp_root=tmp_root,
        caches=runtimes.caches_for(runtime_id),
    )
