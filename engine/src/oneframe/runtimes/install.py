"""Building one runtime's environment on this machine: explicit, resumable and honest.

    <data>/runtimes/<id>/<build>/                        the environment
    <data>/runtimes/<id>/<build>/src/<name>/             pinned sources
    <data>/runtimes/<id>/<build>/stand-ins/<package>/    stand-ins for compiled extensions
    <data>/runtimes/<id>/<build>/oneframe-runtime.json   the marker, written last
    <data>/runtimes/<id>/downloads/<sha256>              source archives
    <data>/runtimes/<id>/downloads/<sha256>.part         one being fetched, kept to resume
    <data>/uv/cache/, <data>/uv/python/                  uv's cache and the Pythons it fetches,
                                                         unless `--uv-home` names another folder

A runtime counts as installed only once the marker exists, and the marker is the last thing
written: it holds its format, the hashes of the lock and definition it was built from, the build,
the environment's own path, and what arrived (a freeze of every package). A marker in a newer
format is left alone, and its runtime is neither installed over nor removed; one whose path is not
where it now lies (a root moved or copied) says the environment must be built again.

An install that stops for any reason -- Stop, a failure, the engine killed -- leaves no marker,
and installing again picks up where it was: uv sync finishes a half-built environment, an archive
already downloaded with the right hash is kept, and one partly fetched resumes from its partial.

Only this module fetches anything (a source, through the model store's transport, retried and
resumed as a model file is), and only when a person asked for the install.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oneframe import archives, processes
from oneframe.environment import uv_environment
from oneframe.errors import Failure, Stopped
from oneframe.files import format_of, free_bytes, remove_tree, replace, sha256_file, write_json
from oneframe.layout import Layout
from oneframe.models.transport import DownloadFailed, fetch
from oneframe.node import Emit

if TYPE_CHECKING:
    from oneframe.runtimes.definition import RuntimeDef, Source

MARKER = "oneframe-runtime.json"
MARKER_FORMAT = 1
PTH = "oneframe-runtime.pth"
STEPS = ("marker", "python", "sync", "sources", "stand-ins", "paths", "freeze", "record")


class InstallFailed(Failure):
    """A step of the install failed (`install_failed`: installing again resumes it), or the
    environment is one this engine must not touch (`newer_format`). Stop raises Stopped, and
    nothing is lost then either."""

    kind = "runtime"

    def __init__(self, message: str, detail: str = "", reason: str = "install_failed"):
        nexts = {"install_failed": "Install again: it picks up where it stopped."}
        super().__init__(message, reason=reason, next=nexts.get(reason))
        self.detail = detail


def runtime_dir(data: Path, runtime_id: str) -> Path:
    return Layout(data).runtimes / runtime_id


def env_dir(data: Path, runtime_id: str, build: str) -> Path:
    return runtime_dir(data, runtime_id) / build


def interpreter(env: Path, platform: str = sys.platform) -> Path:
    return env / ("Scripts/python.exe" if platform == "win32" else "bin/python")


def read_marker(env: Path) -> dict[str, Any] | None:
    try:
        data = json.loads((env / MARKER).read_text(encoding="utf-8"))
    except OSError, ValueError:
        return None
    return data if isinstance(data, dict) else None


def same_path(a: Path | str, b: Path | str) -> bool:
    """Whether two paths name the same place: both resolved, and their case normalised where the
    system ignores case."""
    return os.path.normcase(str(Path(a).resolve())) == os.path.normcase(str(Path(b).resolve()))


def marker_problem(env: Path, marker: dict[str, Any]) -> str | None:
    """`newer_format` for a marker this engine cannot read (a newer one, or one whose format is not
    a number); `moved` for one written for an environment somewhere else; None when it is usable.
    A marker from before formats were recorded is format 1 and says no path."""
    found = format_of(marker, missing=MARKER_FORMAT)
    if found is None or found > MARKER_FORMAT:
        return "newer_format"
    recorded = marker.get("env")
    if isinstance(recorded, str) and not same_path(recorded, env):
        return "moved"
    return None


def run_step(
    argv: list[str], env: dict[str, str], on_line: Callable[[str], None], stop: threading.Event
) -> tuple[int, list[str]]:
    """Run one command to its end, or until Stop, which ends it and everything it started (uv
    starts Python workers to compile bytecode). Its output is passed on line by line; the last
    lines come back with the exit code, for the message when it fails."""
    return processes.run(argv, env=env, on_line=on_line, stop=stop)


def _output(argv: list[str], env: dict[str, str]) -> str:
    """A short command's standard output, or InstallFailed with what it said."""
    done = processes.output(argv, env=env)
    if done.returncode != 0:
        raise InstallFailed(
            f"{Path(argv[0]).name} {argv[1]} failed (exit {done.returncode}).", done.stderr[-4000:]
        )
    return done.stdout


def folder_size(path: Path) -> int:
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            with contextlib.suppress(OSError):
                total += (Path(root) / name).lstat().st_size
    return total


def _freeze(uv: str, python: Path, env: dict[str, str]) -> list[str]:
    return [
        line for line in _output([uv, "pip", "freeze", "--python", str(python)], env).splitlines() if line
    ]


def fetch_source(
    source: Source,
    downloads: Path,
    stop: threading.Event,
    progress: Callable[[int, int | None], None] | None = None,
    log: Callable[[str], None] | None = None,
    transfer: dict[str, Any] | None = None,
) -> Path:
    """`<downloads>/<sha256>`, the source's archive: kept when one is there whose sha256 matches,
    else fetched by the model store's transport into `<downloads>/<sha256>.part`, then moved into
    place. The transport retries, resumes what a Stop or a kill left in the partial, and sees Stop
    at once (Stopped). A failure is InstallFailed. `transfer`: the transport's options (a test's
    TLS context, policy or proxy)."""
    archive = downloads / source.sha256
    partial = downloads / f"{source.sha256}.part"

    def phase(name: str, detail: dict[str, Any]) -> None:
        if name == "retrying" and log is not None:
            log(f"Fetching the source {source.name} again in {detail['wait_s']:g} s: {detail['why']}")

    try:
        if archive.is_file():
            if sha256_file(archive, stop) == source.sha256:
                return archive
            archive.unlink()
        fetch(
            source.url,
            partial,
            None,
            source.sha256,
            stop,
            phase=phase,
            progress=progress,
            free_bytes=lambda: free_bytes(downloads),
            **(transfer or {}),
        )
        replace(partial, archive)
    except (DownloadFailed, OSError) as exc:
        raise InstallFailed(f"The source {source.name} could not be fetched: {exc}") from exc
    return archive


@dataclass(frozen=True)
class _Arrived:
    """What an install brought, as the freeze step reads it and the marker records it."""

    freeze: list[str]
    uv: str
    python: str
    size_bytes: int


class _Install:
    """One install: the runtime, its build and paths, uv's environments, and the helpers each step
    uses. A method per entry of STEPS, called in that order; each says it has started, unless
    Stop was asked for."""

    def __init__(
        self,
        runtime: RuntimeDef,
        build: str,
        data: Path,
        uv: str,
        emit: Emit,
        stop: threading.Event,
        uv_cache: Path,
        uv_python: Path,
        transfer: dict[str, Any] | None,
    ) -> None:
        self.runtime, self.build, self.data, self.uv = runtime, build, data, uv
        self.emit, self.stop, self.transfer = emit, stop, transfer
        self.env = env_dir(data, runtime.id, build)
        self.python = interpreter(self.env)
        self.base = {"runtime": runtime.id, "build": build}
        self.stand_ins = [e for e in runtime.extensions if e.kind == "stand-in"]
        # What uv, and the runtime's own Python, run with; uv sync's also names the environment.
        self.uv_env = uv_environment(uv_cache, uv_python)
        self.sync_env = uv_environment(uv_cache, uv_python, self.env)

    def step(self, name: str, message: str) -> None:
        if self.stop.is_set():
            raise Stopped()
        self.emit(
            {
                **self.base,
                "event": "runtime.step",
                "step": name,
                "index": STEPS.index(name) + 1,
                "total": len(STEPS),
                "message": message,
            }
        )

    def log(self, line: str) -> None:
        self.emit({**self.base, "event": "runtime.log", "line": line})

    def run(self, argv: list[str], what: str, environment: dict[str, str]) -> None:
        code, tail = run_step(argv, environment, self.log, self.stop)
        if code != 0:
            raise InstallFailed(f"{what} failed (exit {code}).", "\n".join(tail))

    def clear_marker(self) -> None:
        self.step("marker", "Clearing the old marker: until the last step, this runtime is not installed.")
        old = read_marker(self.env)
        problem = marker_problem(self.env, old) if old is not None else None
        if problem == "newer_format":
            raise InstallFailed(
                f"{self.env} was installed by a newer version of the app; it is left as it is.",
                reason="newer_format",
            )
        if problem == "moved":
            remove_tree(self.env)  # its scripts and links point into the root it was built in
        (self.env / MARKER).unlink(missing_ok=True)

    def fetch_python(self) -> None:
        python = self.runtime.python
        self.step("python", f"Python {python}, fetched by uv into the data root if it is not there yet.")
        self.run(
            [self.uv, "python", "install", "--no-config", python], f"Installing Python {python}", self.uv_env
        )

    def sync(self) -> None:
        runtime, build = self.runtime, self.build
        self.step("sync", f"The {build} build's packages, exactly as {runtime.lock_path.name} lists them.")
        if self.env.exists() and not (self.env / "pyvenv.cfg").is_file():
            remove_tree(self.env)  # not an environment uv can finish: start it again
        sync = [
            self.uv,
            "sync",
            "--frozen",
            "--no-config",
            "--no-dev",
            "--managed-python",
            "--compile-bytecode",
            "--extra",
            build,
            "--python",
            runtime.python,
            "--project",
            str(runtime.folder),
        ]
        # A failure leaves the folder as it is: uv recreates an environment it cannot use, and a
        # failure for any other reason (the network, a registry) must not cost what is already there.
        self.run(sync, "uv sync", self.sync_env)

    def unpack_sources(self) -> None:
        self.step("sources", f"{len(self.runtime.sources)} pinned source(s), checked by sha256.")
        downloads = runtime_dir(self.data, self.runtime.id) / "downloads"
        for source in self.runtime.sources:
            target = self.env / "src" / source.name
            stamp = self.env / "src" / f"{source.name}.sha256"
            if (
                target.is_dir()
                and stamp.is_file()
                and stamp.read_text(encoding="utf-8").strip() == source.sha256
            ):
                continue

            def progress(done: int, total: int | None, name: str = source.name) -> None:
                self.emit(
                    {
                        **self.base,
                        "event": "runtime.progress",
                        "step": "sources",
                        "what": name,
                        "done": done,
                        "total": total,
                    }
                )

            archive = fetch_source(source, downloads, self.stop, progress, self.log, self.transfer)
            if target.exists():
                remove_tree(target)
            stamp.unlink(missing_ok=True)
            try:
                archives.extract(archive, target)
            except (archives.ArchiveError, OSError) as exc:
                raise InstallFailed(f"The source {source.name} was refused: {exc}") from exc
            stamp.write_text(source.sha256 + "\n", encoding="utf-8")

    def copy_stand_ins(self) -> None:
        self.step("stand-ins", f"{len(self.stand_ins)} stand-in(s) for compiled extensions.")
        for ext in self.stand_ins:
            target = self.env / "stand-ins" / ext.package
            if target.exists():
                remove_tree(target)
            shutil.copytree(self.runtime.folder / ext.stand_in, target)

    def write_paths(self) -> None:
        self.step("paths", "Putting the sources and stand-ins on the runtime's path.")
        env, python = self.env, str(self.python)
        purelib = [python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"]
        site = Path(_output(purelib, self.uv_env).strip())
        lines = [str((env / "src" / s.name / p).resolve()) for s in self.runtime.sources for p in s.paths]
        if self.stand_ins:
            lines.append(str((env / "stand-ins").resolve()))
        if not lines:
            (site / PTH).unlink(missing_ok=True)
            return
        (site / PTH).write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Compiled now, as uv compiled the packages, so a run writes nothing into the runtime. Only
        # a best effort: an upstream archive may hold files that are not this Python's code.
        folders = [str(p) for p in (env / "src", env / "stand-ins") if p.is_dir()]
        compiled, _tail = run_step(
            [python, "-m", "compileall", "-q", *folders], self.uv_env, self.log, self.stop
        )
        if compiled != 0:
            self.log(f"Some source files did not compile (exit {compiled}); they are left as they are.")

    def freeze(self) -> _Arrived:
        """What arrived: every package, the versions of uv and the runtime's Python, and the size."""
        self.step("freeze", "Recording what arrived.")
        version = [str(self.python), "-c", "import sys; print(sys.version.split()[0])"]
        return _Arrived(
            freeze=_freeze(self.uv, self.python, self.uv_env),
            uv=_output([self.uv, "--version"], self.uv_env).strip(),
            python=_output(version, self.uv_env).strip(),
            size_bytes=folder_size(self.env),
        )

    def record(self, arrived: _Arrived) -> None:
        self.step("record", "Writing the marker: the runtime is installed.")
        runtime = self.runtime
        marker = {
            "format": MARKER_FORMAT,
            "runtime": runtime.id,
            "build": self.build,
            "env": str(self.env.resolve()),
            **runtime.hashes(),
            "python": arrived.python,
            "uv": arrived.uv,
            "freeze": arrived.freeze,
            "sources": [{"name": s.name, "url": s.url, "sha256": s.sha256} for s in runtime.sources],
            "stand_ins": [e.package for e in self.stand_ins],
            "left_out": [e.package for e in runtime.extensions if e.kind == "optional"],
            "size_bytes": arrived.size_bytes,
            "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        write_json(self.env / MARKER, marker, indent=2)


def install(
    runtime: RuntimeDef,
    build: str,
    data: Path,
    uv: str,
    emit: Emit,
    stop: threading.Event | None = None,
    *,
    uv_cache: Path,
    uv_python: Path,
    transfer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build `runtime`'s `build` under the data root, with uv's cache and Pythons in the two
    folders given, and its sources fetched with `transfer`'s options. Raises Stopped or
    InstallFailed; the marker exists only when this returns."""
    started = time.monotonic()
    stop = stop if stop is not None else threading.Event()
    job = _Install(runtime, build, data, uv, emit, stop, uv_cache, uv_python, transfer)
    job.clear_marker()
    job.fetch_python()
    job.sync()
    job.unpack_sources()
    job.copy_stand_ins()
    job.write_paths()
    arrived = job.freeze()
    job.record(arrived)
    return {
        **job.base,
        "python": str(job.python),
        "size_bytes": arrived.size_bytes,
        "seconds": round(time.monotonic() - started, 1),
    }
