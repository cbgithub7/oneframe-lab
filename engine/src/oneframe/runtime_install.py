"""Building one runtime's environment on this machine: explicit, resumable and honest.

    <data>/runtimes/<id>/<build>/                        the environment
    <data>/runtimes/<id>/<build>/src/<name>/             pinned sources
    <data>/runtimes/<id>/<build>/stand-ins/<package>/    stand-ins for compiled extensions
    <data>/runtimes/<id>/<build>/oneframe-runtime.json   the marker, written last
    <data>/runtimes/<id>/downloads/<sha256>              source archives
    <data>/uv/cache/, <data>/uv/python/                  uv's cache and the Pythons it fetches

A runtime counts as installed only once the marker exists, and the marker is the last thing
written: it holds the hashes of the lock and definition it was built from, the build, and what
arrived (a freeze of every package). An install that stops for any reason -- Stop, a failure, the
engine killed -- leaves no marker, and installing again picks up where it was: uv sync finishes
a half-built environment, and an archive already downloaded with the right hash is kept.

Only this module fetches anything, and only when a person asked for the install.
"""

from __future__ import annotations

import contextlib
import json
import os
import queue
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oneframe import archives

if TYPE_CHECKING:
    from oneframe.runtimes import RuntimeDef

MARKER = "oneframe-runtime.json"
PTH = "oneframe-runtime.pth"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
NEW_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
# Variables that would point uv, or a Python it starts, at the engine's own environment.
DROP_ENV = ("VIRTUAL_ENV", "CONDA_PREFIX", "PYTHONPATH", "PYTHONHOME", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON")

Emit = Callable[[dict[str, Any]], None]
ShouldStop = Callable[[], bool]
STEPS = ("marker", "python", "sync", "sources", "stand-ins", "paths", "freeze", "record")


class InstallStopped(RuntimeError):
    """Stop was pressed. Nothing is lost: installing again resumes."""


class InstallFailed(RuntimeError):
    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


def runtime_dir(data: Path, runtime_id: str) -> Path:
    return data / "runtimes" / runtime_id


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


def uv_environment(
    uv_home: Path, env: Path | None = None, base: dict[str, str] | None = None
) -> dict[str, str]:
    """uv's cache and Pythons under the data root, where removing the data root removes them, and
    where the cache and the environments share a volume, so uv links files instead of copying.
    uv writes nothing outside it: no `python3.x` link in the person's bin folder, and no entry in
    the Windows registry, either of which would outlive the data root and point at nothing."""
    out = {k: v for k, v in (os.environ if base is None else base).items() if k not in DROP_ENV}
    out["UV_CACHE_DIR"] = str(uv_home / "cache")
    out["UV_PYTHON_INSTALL_DIR"] = str(uv_home / "python")
    out["UV_PYTHON_INSTALL_BIN"] = "0"
    out["UV_PYTHON_INSTALL_REGISTRY"] = "0"
    out["PYTHONIOENCODING"] = "utf-8"
    if env is not None:
        out["UV_PROJECT_ENVIRONMENT"] = str(env)
    return out


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """End a step and everything it started: uv starts Python workers to compile bytecode."""
    if proc.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                capture_output=True,
                check=False,
                creationflags=NO_WINDOW,
            )
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=30)


def run_step(
    argv: list[str], env: dict[str, str], on_line: Callable[[str], None], should_stop: ShouldStop
) -> tuple[int, list[str]]:
    """Run one command to its end, or until Stop. Its output is passed on line by line; the last
    lines come back with the exit code, for the message when it fails."""
    proc = subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        start_new_session=sys.platform != "win32",
        creationflags=(NO_WINDOW | NEW_GROUP) if sys.platform == "win32" else 0,
    )
    lines: queue.Queue[str | None] = queue.Queue()

    def read() -> None:
        assert proc.stdout is not None
        for line in iter(proc.stdout.readline, ""):
            lines.put(line.rstrip())
        lines.put(None)

    threading.Thread(target=read, daemon=True).start()
    tail: deque[str] = deque(maxlen=40)
    try:
        while True:
            if should_stop():
                raise InstallStopped()
            try:
                line = lines.get(timeout=0.2)
            except queue.Empty:
                continue
            if line is None:
                break
            if line:
                tail.append(line)
                on_line(line)
        return proc.wait(), list(tail)
    finally:
        _kill_tree(proc)


def _output(argv: list[str], env: dict[str, str]) -> str:
    """A short command's standard output, or InstallFailed with what it said."""
    done = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        stdin=subprocess.DEVNULL,
        check=False,
        creationflags=NO_WINDOW,
    )
    if done.returncode != 0:
        raise InstallFailed(
            f"{Path(argv[0]).name} {argv[1]} failed (exit {done.returncode}).", done.stderr[-4000:]
        )
    return done.stdout


def _clear_readonly(func: Callable[..., Any], path: str, _exc: BaseException) -> None:
    Path(path).chmod(stat.S_IWRITE)
    func(path)


def remove_tree(path: Path) -> None:
    """rmtree that also removes read-only files, which Windows refuses to delete otherwise."""
    shutil.rmtree(path, onexc=_clear_readonly)


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


def install(
    runtime: RuntimeDef,
    build: str,
    data: Path,
    uv: str,
    emit: Emit,
    should_stop: ShouldStop = lambda: False,
    uv_home: Path | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> dict[str, Any]:
    """Build `runtime`'s `build` under the data root. Raises InstallStopped or InstallFailed; the
    marker exists only when this returns."""
    started = time.monotonic()
    uv_home = uv_home or data / "uv"
    env = env_dir(data, runtime.id, build)
    python = interpreter(env)
    base = {"runtime": runtime.id, "build": build}

    def step(name: str, message: str) -> None:
        if should_stop():
            raise InstallStopped()
        emit(
            {
                **base,
                "event": "runtime.step",
                "step": name,
                "index": STEPS.index(name) + 1,
                "total": len(STEPS),
                "message": message,
            }
        )

    def log(line: str) -> None:
        emit({**base, "event": "runtime.log", "line": line})

    def run(argv: list[str], what: str, environment: dict[str, str]) -> None:
        code, tail = run_step(argv, environment, log, should_stop)
        if code != 0:
            raise InstallFailed(f"{what} failed (exit {code}).", "\n".join(tail))

    step("marker", "Clearing the old marker: until the last step, this runtime is not installed.")
    (env / MARKER).unlink(missing_ok=True)

    step("python", f"Python {runtime.python}, fetched by uv into the data root if it is not there yet.")
    run(
        [uv, "python", "install", "--no-config", "--no-bin", "--no-registry", runtime.python],
        f"Installing Python {runtime.python}",
        uv_environment(uv_home),
    )

    step("sync", f"The {build} build's packages, exactly as {runtime.lock_path.name} lists them.")
    if env.exists() and not (env / "pyvenv.cfg").is_file():
        remove_tree(env)  # not an environment uv can finish: start it again
    sync = [
        uv,
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
    code, tail = run_step(sync, uv_environment(uv_home, env), log, should_stop)
    if code != 0:
        raise InstallFailed(f"uv sync failed (exit {code}).", "\n".join(tail))

    step("sources", f"{len(runtime.sources)} pinned source(s), checked by sha256.")
    downloads = runtime_dir(data, runtime.id) / "downloads"
    for source in runtime.sources:
        target = env / "src" / source.name
        stamp = env / "src" / f"{source.name}.sha256"
        if target.is_dir() and stamp.is_file() and stamp.read_text(encoding="utf-8").strip() == source.sha256:
            continue

        def progress(done: int, total: int | None, name: str = source.name) -> None:
            emit(
                {
                    **base,
                    "event": "runtime.progress",
                    "step": "sources",
                    "what": name,
                    "done": done,
                    "total": total,
                }
            )

        try:
            archive = archives.download(
                source.url, source.sha256, downloads / source.sha256, progress, should_stop, opener
            )
        except archives.Stopped as exc:
            raise InstallStopped() from exc
        except (archives.ArchiveError, OSError) as exc:
            raise InstallFailed(f"The source {source.name} could not be fetched: {exc}") from exc
        if target.exists():
            remove_tree(target)
        stamp.unlink(missing_ok=True)
        try:
            archives.extract(archive, target)
        except (archives.ArchiveError, OSError) as exc:
            raise InstallFailed(f"The source {source.name} was refused: {exc}") from exc
        stamp.write_text(source.sha256 + "\n", encoding="utf-8")

    stand_ins = [e for e in runtime.extensions if e.kind == "stand-in"]
    step("stand-ins", f"{len(stand_ins)} stand-in(s) for compiled extensions.")
    for ext in stand_ins:
        target = env / "stand-ins" / ext.package
        if target.exists():
            remove_tree(target)
        shutil.copytree(runtime.folder / ext.stand_in, target)

    step("paths", "Putting the sources and stand-ins on the runtime's path.")
    site = Path(
        _output(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
            uv_environment(uv_home),
        ).strip()
    )
    lines = [str((env / "src" / s.name / p).resolve()) for s in runtime.sources for p in s.paths]
    if stand_ins:
        lines.append(str((env / "stand-ins").resolve()))
    if lines:
        (site / PTH).write_text("\n".join(lines) + "\n", encoding="utf-8")
        # Compiled now, as uv compiled the packages, so a run writes nothing into the runtime. Only
        # a best effort: an upstream archive may hold files that are not this Python's code.
        folders = [str(p) for p in (env / "src", env / "stand-ins") if p.is_dir()]
        compiled, _tail = run_step(
            [str(python), "-m", "compileall", "-q", *folders], uv_environment(uv_home), log, should_stop
        )
        if compiled != 0:
            log(f"Some source files did not compile (exit {compiled}); they are left as they are.")
    else:
        (site / PTH).unlink(missing_ok=True)

    step("freeze", "Recording what arrived.")
    freeze = _freeze(uv, python, uv_environment(uv_home))
    uv_version = _output([uv, "--version"], uv_environment(uv_home)).strip()
    python_version = _output(
        [str(python), "-c", "import sys; print(sys.version.split()[0])"], uv_environment(uv_home)
    ).strip()
    size = folder_size(env)

    step("record", "Writing the marker: the runtime is installed.")
    marker = {
        "runtime": runtime.id,
        "build": build,
        **runtime.hashes(),
        "python": python_version,
        "uv": uv_version,
        "freeze": freeze,
        "sources": [{"name": s.name, "url": s.url, "sha256": s.sha256} for s in runtime.sources],
        "stand_ins": [e.package for e in stand_ins],
        "left_out": [e.package for e in runtime.extensions if e.kind == "optional"],
        "size_bytes": size,
        "installed_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    part = env / (MARKER + ".part")
    part.write_text(json.dumps(marker, indent=2), encoding="utf-8")
    part.replace(env / MARKER)
    return {
        **base,
        "python": str(python),
        "size_bytes": size,
        "seconds": round(time.monotonic() - started, 1),
    }
