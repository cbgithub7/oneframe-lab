"""Download and verify, each a job (spec 003, requirements 3 and 4).

    models.download {node, checkpoints?}   the files every run of the node reads, and the default
                                           checkpoint's; a list of checkpoints, or "all"
    models.verify {node?}                  hash again what the store holds, of one node or all
    models.stop {job?}

A job answers at once with its id, which every `model.*` event carries: `model.start`, then
`model.file` with a phase (checking, downloading, retrying, verifying or placing), throttled
`model.progress`, and one ending, `model.done`, `model.failed` or `model.stopped`, each listing
every file's outcome. `model.failed` takes the kind and reason of the first file that failed; a
file that fails does not stop the others. Every event also goes to the job's journal.

One job runs at a time across processes: a second is refused, `busy` in this engine and `locked`
from another process. The lock is the store's, so remove and pin take it too. A job lets go of the
lock and its place before its ending is sent, so a next job asked for on hearing it starts; that
job's events may then come before the ending, and a listener tells jobs apart by `job`.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe.errors import Failure, Stopped
from oneframe.files import FileLock, lock_file
from oneframe.journal import engine_crash, journalled
from oneframe.manifest import Manifest
from oneframe.models.schema import FileSpec
from oneframe.models.store import Store, StoreRefused, hash_file
from oneframe.models.transport import DownloadFailed, Transfer
from oneframe.node import Emit

LOGGER = logging.getLogger(__name__)

HUB = "https://huggingface.co"
LOCK = "models"
PROGRESS_EVERY_S = 0.25  # model.progress at most this often, and at each file's end


def hub_endpoint() -> str:
    """The hub, or the mirror HF_ENDPOINT names; read when a job starts."""
    return (os.environ.get("HF_ENDPOINT") or HUB).rstrip("/")


def files_to_fetch(manifest: Manifest, checkpoints: list[str] | str | None) -> list[FileSpec]:
    """The files a download fetches: every shared file, and the default checkpoint's, the named
    checkpoints' or ("all") every checkpoint's; each sha256 once."""
    if checkpoints == "all":
        wanted = [f for f in manifest.files]
    else:
        chosen = set(checkpoints or [c for c in [manifest.default_checkpoint] if c is not None])
        wanted = [f for f in manifest.files if not f.checkpoints or chosen & set(f.checkpoints)]
    seen: set[str] = set()
    unique = []
    for spec in wanted:
        if spec.sha256 not in seen:
            seen.add(spec.sha256)
            unique.append(spec)
    return unique


def store_lock(store: Store) -> FileLock:
    return FileLock(lock_file(store.layout.locks, LOCK))


def claim_store(store: Store, busy_here: str | None) -> FileLock:
    """The store's lock, for a job, a remove or a pin: refused `busy` when this engine is running a
    job, `locked` when another process holds it; then the store's own format checked."""
    if busy_here is not None:
        raise StoreRefused(
            f"Job {busy_here} is still running; downloads and verifies run one at a time.",
            "busy",
            "Wait for it, or stop it.",
        )
    lock = store_lock(store)
    if not lock.acquire():
        raise StoreRefused(
            "Another process on this data root is downloading, verifying or removing model files.",
            "locked",
            "Try again when it is done.",
        )
    try:
        store.claim()
    except BaseException:
        lock.release()
        raise
    return lock


@dataclass
class Outcome:
    name: str | None
    sha256: str
    size: int
    outcome: str = "waiting"  # present, downloaded, verified, dropped, missing, failed, stopped
    failure: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "name": self.name,
            "sha256": self.sha256,
            "size": self.size,
            "outcome": self.outcome,
        }
        if self.failure is not None:
            row["failure"] = self.failure
        return row


@dataclass
class Job:
    id: str
    what: str  # download or verify
    node: str | None
    files: list[Outcome]
    specs: dict[str, FileSpec] = field(default_factory=dict)  # by sha256, for a download
    stop: threading.Event = field(default_factory=threading.Event)


class Jobs:
    """This engine's model jobs: at most one at a time, run in its own thread."""

    def __init__(
        self,
        data: Path,
        say: Emit,
        transfer: dict[str, Any] | None = None,
        in_use: Callable[[], set[str]] | None = None,
    ):
        """`transfer`: options for each Transfer (a test's TLS context, policy or proxy).
        `in_use`: the sha256 of every file a run in any process reads (task 7); verify drops only
        the record of such a file."""
        self.data = data
        self.store = Store(data)
        self.say = say
        self.transfer = dict(transfer or {})
        self.in_use = in_use or (lambda: set())
        self._lock = threading.Lock()
        self._job: Job | None = None
        self._thread: threading.Thread | None = None

    def running(self) -> str | None:
        with self._lock:
            return None if self._job is None else self._job.id

    # starting

    def download(self, manifest: Manifest, checkpoints: list[str] | str | None = None) -> dict[str, Any]:
        if checkpoints not in (None, "all") and not isinstance(checkpoints, list):
            raise Failure(
                "checkpoints should be a list of checkpoints, or 'all'.", "bad_params", kind="request"
            )
        if isinstance(checkpoints, list):
            choices = manifest.params[manifest.files_by].choices if manifest.files_by else ()
            unknown = [c for c in checkpoints if c not in choices]
            if unknown:
                raise Failure(
                    f"{manifest.id} has no checkpoint {', '.join(map(repr, unknown))}.",
                    "bad_params",
                    kind="request",
                )
        specs = files_to_fetch(manifest, checkpoints)
        files = [Outcome(s.name, s.sha256, s.size) for s in specs]
        job = Job(uuid.uuid4().hex[:12], "download", manifest.id, files, {s.sha256: s for s in specs})
        return self._start(job)

    def verify(self, node: str | None, manifests: list[Manifest]) -> dict[str, Any]:
        """Hash again every stored file of `node` (all its checkpoints'), or with None, every file in
        the store. `manifests`: the node's, or every node's, which name the files."""
        names: dict[str, FileSpec] = {}
        for manifest in manifests:
            for spec in manifest.files:
                names.setdefault(spec.sha256, spec)
        shas = list(names) if node is not None else self.store.stored()
        files = [
            Outcome(names[sha].name if sha in names else None, sha, names[sha].size if sha in names else 0)
            for sha in shas
        ]
        return self._start(Job(uuid.uuid4().hex[:12], "verify", node, files))

    def _start(self, job: Job) -> dict[str, Any]:
        with self._lock:
            held = claim_store(self.store, None if self._job is None else self._job.id)
            self._job = job
        thread = threading.Thread(target=self._run, args=(job, held), name=f"model-{job.id}", daemon=True)
        self._thread = thread
        thread.start()
        return {"job": job.id, "files": [f.to_json() for f in job.files]}

    def stop(self, job_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            job = self._job
            if job is None or (job_id and job_id != job.id):
                return {"stopping": False}
            job.stop.set()
            return {"stopping": True, "job": job.id}

    def shutdown(self, timeout: float) -> None:
        """Stop a running job and wait for it, so its journal ends with `model.stopped`."""
        with self._lock:
            job, thread = self._job, self._thread
        if job is not None:
            job.stop.set()
        if thread is not None:
            thread.join(timeout)

    # running

    def _run(self, job: Job, held: FileLock) -> None:
        what = f"model-{job.what}-{job.node or 'all'}"
        crashed: Callable[[BaseException], dict[str, Any]] = lambda exc: engine_crash(  # noqa: E731
            exc, event="model.failed", job=job.id, files=[f.to_json() for f in job.files]
        )
        ending: list[dict[str, Any]] = []  # journalled first, sent once the store is free

        def say(event: dict[str, Any]) -> None:
            if not (ending and event is ending[0]):
                self.say(event)

        try:
            with journalled(say, self.store.layout.journals, what, crashed) as emit:
                ending.append(self._work(job, emit))
                emit(ending[0])  # the journal's last line, not yet sent
                self._free(job, held)  # whoever hears the ending may start the next job at once
                self.say(ending[0])  # in the block: a listener that raises is journalled as a bug
        except Exception as exc:  # a bug: the journal ends with it, and the page hears of it too
            LOGGER.exception("model job %s crashed", job.id)
            try:
                self._free(job, held)
            finally:
                self.say(crashed(exc))
        finally:
            self._free(job, held)

    def _free(self, job: Job, held: FileLock) -> None:
        """Let go of the store's lock and of this job's place, together under the lock `_start`
        claims both with, so it never sees one free without the other. The place only while it is
        still this job's: a next job may have started on hearing this one end. Safe to repeat."""
        with self._lock:
            try:
                held.release()
            finally:
                if self._job is job:
                    self._job = None

    def _work(self, job: Job, emit: Emit) -> dict[str, Any]:
        """Run the job, sending everything but its ending, which it returns for `_run` to send."""
        base = {"job": job.id, "what": job.what, "node": job.node}
        emit(
            {
                **base,
                "event": "model.start",
                "files": [f.to_json() for f in job.files],
                "bytes": sum(f.size for f in job.files),
            }
        )
        first: dict[str, Any] | None = None
        stopped = False
        for outcome in job.files:
            if job.stop.is_set():
                stopped = True
                break
            try:
                if job.what == "download":
                    self._download(job, outcome, emit)
                else:
                    self._verify(job, outcome, emit)
            except Stopped:
                outcome.outcome = "stopped"
                stopped = True
                break
            except (DownloadFailed, StoreRefused, OSError) as exc:
                failed: Failure = (
                    # The system refused to read or write one file (an antivirus holding it, a bad
                    # sector): that file fails, the others go on, and it is no engine bug.
                    Failure(f"The system refused {outcome.name or outcome.sha256[:12]}: {exc}", kind="store")
                    if isinstance(exc, OSError)
                    else exc
                )
                outcome.outcome = "failed"
                outcome.failure = failed.to_json(
                    **({"kept": exc.kept} if isinstance(exc, DownloadFailed) else {})
                )
                first = first or outcome.failure
                emit(
                    {
                        **base,
                        "event": "model.file",
                        "file": outcome.name,
                        "sha256": outcome.sha256,
                        "phase": "failed",
                        **outcome.failure,
                    }
                )
        files = [f.to_json() for f in job.files]
        if stopped:
            for outcome in job.files:
                if outcome.outcome == "waiting":
                    outcome.outcome = "stopped"
            files = [f.to_json() for f in job.files]
            return {
                **base,
                "event": "model.stopped",
                "files": files,
                "message": "Stopped; a download resumes where it stopped.",
            }
        if first is not None:
            return {**base, "event": "model.failed", **first, "files": files}
        done: dict[str, Any] = {**base, "event": "model.done", "files": files}
        if job.what == "verify":
            done["dropped"] = [f.sha256 for f in job.files if f.outcome == "dropped"]
        return done

    def _phase(self, job: Job, outcome: Outcome, emit: Emit, phase: str, **detail: Any) -> None:
        emit(
            {
                "job": job.id,
                "event": "model.file",
                "file": outcome.name,
                "sha256": outcome.sha256,
                "phase": phase,
                **detail,
            }
        )

    def _progress(self, job: Job, outcome: Outcome, emit: Emit) -> Callable[[int, int | None], None]:
        last = [0.0]

        def progress(have: int, total: int | None) -> None:
            now = time.monotonic()
            if now - last[0] < PROGRESS_EVERY_S and (total is None or have < total):
                return
            last[0] = now
            emit(
                {
                    "job": job.id,
                    "event": "model.progress",
                    "file": outcome.name,
                    "sha256": outcome.sha256,
                    "bytes": have,
                    "total": total,
                }
            )

        return progress

    def _download(self, job: Job, outcome: Outcome, emit: Emit) -> None:
        spec = job.specs[outcome.sha256]
        sha = spec.sha256
        found = self.store.presence(sha, spec.size)
        if found.status == "present":
            outcome.outcome = "present"
            return
        if found.status == "newer":
            raise StoreRefused(
                f"{spec.name} was recorded by a newer version of the app; it was left as it is.",
                "newer_format",
                "Use the newer version of the app.",
            )
        if found.status == "changed":
            self._phase(job, outcome, emit, "checking", bytes=spec.size)
            if self.store.adopt(sha, job.stop):
                outcome.outcome = "present"
                return
        transfer = Transfer(
            spec.source(hub_endpoint()),
            self.store.partial(sha),
            spec.size,
            sha,
            job.stop,
            phase=lambda name, detail: self._phase(job, outcome, emit, name, **detail),
            progress=self._progress(job, outcome, emit),
            free_bytes=self.store.free_bytes,
            **self.transfer,
        )
        transfer.run()
        self._phase(job, outcome, emit, "verifying")  # the sha256 was kept as the bytes came in
        self._phase(job, outcome, emit, "placing")
        self.store.place(self.store.partial(sha), sha)
        outcome.outcome = "downloaded"

    def _verify(self, job: Job, outcome: Outcome, emit: Emit) -> None:
        sha = outcome.sha256
        record = self.store.record(sha)
        if record == "newer":
            raise StoreRefused(
                f"{outcome.name or sha[:12]} was recorded by a newer version of the app; it was left as it "
                "is.",
                "newer_format",
                "Use the newer version of the app.",
            )
        blob = self.store.blob(sha)
        if not blob.is_file():
            outcome.outcome = "missing"
            return
        outcome.size = outcome.size or blob.stat().st_size
        self._phase(job, outcome, emit, "verifying", bytes=outcome.size)
        progress = self._progress(job, outcome, emit)
        if hash_file(blob, job.stop, lambda have: progress(have, outcome.size)) == sha:
            self.store.recorded(sha)
            outcome.outcome = "verified"
            return
        # A run in any process may be reading it: then only its record goes, and the file once no
        # run uses it (task 7's remove, or the next verify).
        if sha in self.in_use():
            self.store.drop_record(sha)
        else:
            self.store.drop(sha)
        outcome.outcome = "dropped"
