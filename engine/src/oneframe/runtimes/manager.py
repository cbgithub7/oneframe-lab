"""The manager: every runtime this engine knows and its state on this machine; status, install
and remove; and what the scheduler and its executors ask of a node's runtime."""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from oneframe import RUNTIMES_DIR, hardware
from oneframe.errors import Failure, Stopped
from oneframe.files import FileLock, lock_file
from oneframe.journal import end_when_free, engine_crash
from oneframe.layout import Layout
from oneframe.memory.estimate import Target
from oneframe.node import Emit
from oneframe.runtimes import install as runtime_install
from oneframe.runtimes.definition import RuntimeDef, RuntimeSet, discover
from oneframe.runtimes.plan import Plan, plan, planned_card


class RuntimeMissing(Failure, RuntimeError):
    """A node's runtime cannot run yet: kind `runtime`, with a reason declared in errors.py
    (not_installed, out_of_date, installing, blocked, unknown, moved, newer_format)."""

    kind = "runtime"

    def __init__(self, message: str, reason: str = "not_installed", next: str | None = None):
        super().__init__(message, reason=reason, next=next)


class InstallRefused(Failure, ValueError):
    """An install or remove that cannot start: kind `runtime`, with a reason declared in
    errors.py (locked when another process is installing or removing it, busy, blocked, ...)."""

    kind = "runtime"

    def __init__(self, message: str, reason: str, next: str | None = None):
        super().__init__(message, reason=reason, next=next)


class RuntimeSide(Protocol):
    """What the scheduler and its executors ask of the runtimes: `Runtimes` answers from what this
    machine installed, `NoRuntimes` with nothing installed, and a test with its double."""

    def python_for(self, runtime_id: str) -> Path: ...

    def env_for(self, runtime_id: str) -> dict[str, str]: ...

    def caches_for(self, runtime_id: str) -> Path | None: ...

    def key_for(self, runtime_id: str) -> dict[str, str] | None: ...

    def device_target(self, runtime_id: str) -> Target: ...


class NoRuntimes:
    """No runtime installed: a runtime node fails `not_installed` where it needs its runtime, while
    its cache key and its fit's target answer as for a runtime with no build and no card, so the
    executors a test gives the scheduler still get the job."""

    def _missing(self, runtime_id: str) -> RuntimeMissing:
        return RuntimeMissing(f"The runtime {runtime_id!r} is not installed.")

    def python_for(self, runtime_id: str) -> Path:
        raise self._missing(runtime_id)

    def env_for(self, runtime_id: str) -> dict[str, str]:
        raise self._missing(runtime_id)

    def caches_for(self, runtime_id: str) -> Path | None:
        raise self._missing(runtime_id)

    def key_for(self, runtime_id: str) -> dict[str, str] | None:
        return None

    def device_target(self, runtime_id: str) -> Target:
        return Target()


def find_uv(explicit: str | None = None, env: dict[str, str] | None = None) -> str | None:
    """uv: as given, else the UV variable `uv run` sets for the engine, else ONEFRAME_UV, else PATH."""
    source = os.environ if env is None else env
    return explicit or source.get("UV") or source.get("ONEFRAME_UV") or shutil.which("uv")


class Runtimes:
    """Every runtime this engine knows, and its state on this machine.

    Definitions are read again on every call, so a lock changed under a running engine counts at
    once. The machine profile is read on first use and when asked to refresh. Listing and planning
    start no process but nvidia-smi and open no connection; only install fetches anything."""

    def __init__(
        self,
        data: Path,
        roots: list[Path] | None = None,
        uv: str | None = None,
        profile: Callable[[], dict[str, Any]] | None = None,
        uv_home: Path | None = None,
        transfer: dict[str, Any] | None = None,
    ):
        """`transfer`: options for each source's download, as model jobs take them (a test's TLS
        context, policy or proxy)."""
        # Absolute, because a runtime's child runs in a folder of its own: a relative path to the
        # probe or the interpreter would point somewhere else from there.
        self.data = Path(data).resolve()
        self.roots = [Path(r).resolve() for r in roots] if roots is not None else [RUNTIMES_DIR]
        self.uv = uv
        # uv's cache and Pythons: the layout's, or folders of the same names under --uv-home's
        layout = Layout(self.data)
        if uv_home is None:
            self.uv_cache, self.uv_python = layout.uv_cache, layout.uv_python
        else:
            home = Path(uv_home).resolve()
            self.uv_cache, self.uv_python = home / layout.uv_cache.name, home / layout.uv_python.name
        self.transfer = dict(transfer or {})
        self._read_profile = profile or (lambda: hardware.profile(data))
        self._profile: dict[str, Any] | None = None
        # The install slot's. Re-entrant: an install lets go of its slot and sends its ending under
        # it, so a listener that hears the ending may claim at once, and another thread waits for it.
        self._lock = threading.RLock()
        self._busy: tuple[str, threading.Event] | None = None
        self._held: FileLock | None = None  # the busy runtime's lock, across processes

    # reading

    def definitions(self) -> RuntimeSet:
        return discover(self.roots)

    def profile(self, refresh: bool = False) -> dict[str, Any]:
        if self._profile is None or refresh:
            self._profile = self._read_profile()
        return self._profile

    def get(self, runtime_id: str) -> RuntimeDef:
        found = self.definitions()
        if runtime_id not in found.runtimes:
            problems = next(
                (p for path, p in found.problems.items() if Path(path).parent.name == runtime_id), None
            )
            detail = f": {'; '.join(problems)}" if problems else ""
            raise RuntimeMissing(f"No runtime called {runtime_id!r} is defined{detail}.", reason="unknown")
        return found.runtimes[runtime_id]

    def _markers(self, runtime: RuntimeDef) -> dict[str, dict[str, Any]]:
        out = {}
        for build in runtime.builds:
            marker = runtime_install.read_marker(runtime_install.env_dir(self.data, runtime.id, build.name))
            if marker is not None:
                out[build.name] = marker
        return out

    def plan_for(self, runtime: RuntimeDef, refresh: bool = False) -> Plan:
        return plan(runtime, self.profile(refresh), installed=set(self._markers(runtime)))

    def installing(self) -> str | None:
        busy = self._busy
        return busy[0] if busy else None

    def status(self, runtime: RuntimeDef, the_plan: Plan | None = None) -> dict[str, Any]:
        """installing, installed, out_of_date, not_installed, newer_format (its marker is from a
        newer app) or moved (built in another root), for the build the plan picks."""
        the_plan = the_plan or self.plan_for(runtime)
        markers = self._markers(runtime)
        build = the_plan.build
        on_disk = [
            b.name for b in runtime.builds if runtime_install.env_dir(self.data, runtime.id, b.name).is_dir()
        ]
        marker = markers.get(build) if build else None
        problem = (
            runtime_install.marker_problem(runtime_install.env_dir(self.data, runtime.id, str(build)), marker)
            if marker is not None
            else None
        )
        hashes = runtime.hashes()
        if self.installing() == runtime.id:
            state = "installing"
        elif marker is None:
            state = "not_installed"
        elif problem is not None:
            state = problem
        elif {k: marker.get(k) for k in hashes} != hashes:
            state = "out_of_date"
        elif not runtime_install.interpreter(
            runtime_install.env_dir(self.data, runtime.id, str(build))
        ).exists():
            state = "not_installed"
        else:
            state = "installed"
        return {
            "status": state,
            "build": build or next(iter(markers), None),
            "size_bytes": marker.get("size_bytes") if marker else None,
            "on_disk": on_disk,
            "installed": {
                name: {"python": m.get("python"), "installed_at": m.get("installed_at")}
                for name, m in markers.items()
            },
        }

    def list(self) -> dict[str, Any]:
        found = self.definitions()
        rows = []
        for runtime in found.runtimes.values():
            the_plan = self.plan_for(runtime)
            rows.append(
                {
                    "id": runtime.id,
                    "title": runtime.title,
                    "summary": runtime.summary,
                    "python": runtime.python,
                    **self.status(runtime, the_plan),
                    "plan": the_plan.to_json(),
                }
            )
        return {"runtimes": rows, "problems": found.problems, "profile": self.profile_summary()}

    def profile_summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.profile().items() if k != "raw"}

    def plans(self, runtime_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
        self.profile(refresh)
        found = self.definitions()
        chosen = [self.get(runtime_id)] if runtime_id else list(found.runtimes.values())
        return {"plans": [self.plan_for(r).to_json() for r in chosen], "profile": self.profile_summary()}

    # what the scheduler asks

    def python_for(self, runtime_id: str) -> Path:
        """The interpreter of the build this machine runs, or RuntimeMissing saying why not."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        if the_plan.blocked:
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} cannot run here: {the_plan.blocked}", reason="blocked"
            )
        state = self.status(runtime, the_plan)["status"]
        install = f"Install the runtime {runtime_id}."
        if state == "installing":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is being installed; run again when it is done.",
                "installing",
                "Run again when the install is done.",
            )
        if state == "newer_format":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} was installed by a newer version of the app, and is left "
                "as it is. Use that version, or another data root.",
                reason="newer_format",
            )
        if state == "moved":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} was built in another data root and moved here; install "
                "it again to rebuild it.",
                reason="moved",
                next=install,
            )
        if state == "out_of_date":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is out of date: its lock or definition changed since it "
                "was installed. Install it again to rebuild it.",
                reason="out_of_date",
                next=install,
            )
        if state != "installed":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is not installed; install its {the_plan.build} build first.",
                reason="not_installed",
                next=install,
            )
        return runtime_install.interpreter(
            runtime_install.env_dir(self.data, runtime.id, str(the_plan.build))
        )

    def env_for(self, runtime_id: str) -> dict[str, str]:
        """runtime.json's variables. On a machine with several NVIDIA cards, also the card the plan
        was made for, so that a node's "cuda" is that card: CUDA numbers cards fastest first unless
        told to use the PCI order nvidia-smi reports them in."""
        runtime = self.get(runtime_id)
        env = dict(runtime.env)
        profile = self.profile()
        cards = [g for g in profile.get("gpus") or [] if g.get("vendor") == "nvidia"]
        the_plan = self.plan_for(runtime)
        build = runtime.build(str(the_plan.build)) if the_plan.build else None
        card = planned_card(profile)
        if len(cards) > 1 and card is not None and build is not None and build.vendor == "nvidia":
            env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
            env.setdefault("CUDA_VISIBLE_DEVICES", str(card.get("index", 0)))
        return env

    def key_for(self, runtime_id: str) -> dict[str, str]:
        """What a node's cache key takes from its runtime: the build this machine runs, and the
        hashes of the lock, definition and stand-ins that build was installed from."""
        runtime = self.get(runtime_id)
        build = str(self.plan_for(runtime).build)
        marker = self._markers(runtime).get(build) or {}
        return {"build": build, **{k: str(marker.get(k, "")) for k in runtime.hashes()}}

    def caches_for(self, runtime_id: str) -> Path:
        """Where a node in this runtime keeps its libraries' caches: `cache/runtime/<id>/`."""
        return Layout(self.data).runtime_caches / runtime_id

    def device_target(self, runtime_id: str) -> Target:
        """What a node in this runtime can use, for the fit: the card the plan's build was made
        for (none for a processor build, or a machine without one), and the lock it was built from,
        which what a machine learns is kept under."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        build = runtime.build(str(the_plan.build)) if the_plan.build else None
        card = planned_card(self.profile())
        lock = f"{the_plan.build}:{runtime.hashes()['lock_sha256'][:16]}"
        if build is None or build.vendor != "nvidia" or card is None:
            return Target(card=None, lock=lock)
        return Target(card=int(card.get("index", 0)), lock=lock)

    # changing

    def _runtime_lock(self, runtime_id: str) -> FileLock:
        """Held across processes while a runtime is installed or removed; a second is refused."""
        lock = FileLock(lock_file(Layout(self.data).locks, f"runtime-{runtime_id}"))
        if not lock.acquire():
            raise InstallRefused(
                f"{runtime_id} is being installed or removed by another process on this data root; "
                "try again when it is done.",
                reason="locked",
                next="Try again when it is done.",
            )
        return lock

    def begin_install(
        self, runtime_id: str, build: str | None = None
    ) -> tuple[RuntimeDef, str, threading.Event] | None:
        """Check an install can start and claim the one install slot. None: already installed."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        if the_plan.blocked:
            raise InstallRefused(the_plan.blocked, "blocked")
        planned = str(the_plan.build)
        if build is not None and build != planned:
            if runtime.build(build) is None:
                raise InstallRefused(
                    f"The runtime {runtime_id!r} has no build called {build!r}.", "wrong_build"
                )
            why = next((r["why"] for r in the_plan.considered if r["build"] == build), "")
            raise InstallRefused(
                f"This machine runs the {planned} build of {runtime_id!r}; "
                f"only the planned build is installed. {why}".strip(),
                "wrong_build",
            )
        if not uv_available(self.uv):
            raise InstallRefused("uv was not found.", "no_uv", "Install uv, or set ONEFRAME_UV to its path.")
        with self._lock:
            if self._busy is not None:
                raise InstallRefused(
                    f"{self._busy[0]} is being installed; runtimes are installed one at a time.",
                    "busy",
                    "Try again when it is done.",
                )
            held = self._runtime_lock(runtime_id)
            try:
                state = self.status(runtime, the_plan)["status"]
                if state == "newer_format":
                    raise InstallRefused(
                        f"The runtime {runtime_id!r} was installed by a newer version of the app; it "
                        "is left as it is.",
                        "newer_format",
                    )
            except BaseException:
                held.release()
                raise
            if state == "installed":
                held.release()
                return None
            stop = threading.Event()
            self._busy = (runtime_id, stop)
            self._held = held
        return runtime, planned, stop

    def run_install(
        self,
        runtime: RuntimeDef,
        build: str,
        stop: threading.Event,
        emit: Emit,
    ) -> dict[str, Any] | None:
        """Carry out a claimed install, reporting it as runtime.* events. Returns the result, or
        None when it stopped or failed (the events say which). The slot and the runtime's lock go
        before the ending is sent, so a next install may start at once."""
        base = {"runtime": runtime.id, "build": build}

        def work(kept: Emit) -> tuple[dict[str, Any] | None, dict[str, Any]]:
            kept({**base, "event": "runtime.start"})
            try:
                done = runtime_install.install(
                    runtime,
                    build,
                    self.data,
                    str(self.uv),
                    kept,
                    stop,
                    uv_cache=self.uv_cache,
                    uv_python=self.uv_python,
                    transfer=self.transfer,
                )
            except Stopped:
                message = "Install stopped. Installing again picks up where it was."
                return None, {**base, "event": "runtime.stopped", "message": message}
            except runtime_install.InstallFailed as exc:
                return None, {**base, "event": "runtime.failed", **exc.to_json(detail=exc.detail)}
            return done, {**base, "event": "runtime.done", **done}

        def release() -> None:
            self._busy = None
            held, self._held = self._held, None
            if held is not None:
                held.release()

        def crashed(exc: BaseException) -> dict[str, Any]:
            return engine_crash(exc, **base, event="runtime.failed")

        journals = Layout(self.data).journals
        return end_when_free(
            emit, journals, f"install-{runtime.id}", work, release, crashed, under=self._lock
        )

    def install(
        self, runtime_id: str, build: str | None = None, emit: Emit = lambda _e: None
    ) -> dict[str, Any] | None:
        """Install and wait: begin_install, then run_install on this thread."""
        claimed = self.begin_install(runtime_id, build)
        if claimed is None:
            return {"runtime": runtime_id, "already": True}
        return self.run_install(*claimed, emit=emit)

    def stop(self, runtime_id: str) -> bool:
        busy = self._busy
        if busy is None or busy[0] != runtime_id:
            return False
        busy[1].set()
        return True

    def remove(self, runtime_id: str) -> dict[str, Any]:
        """Delete `<data>/runtimes/<id>/`, and nothing else."""
        self.get(runtime_id)
        if self.installing() == runtime_id:
            raise InstallRefused(
                f"{runtime_id} is being installed; stop the install before removing it.", "installing"
            )
        with self._runtime_lock(runtime_id):
            return self._remove(runtime_id)

    def _remove(self, runtime_id: str) -> dict[str, Any]:
        root = Layout(self.data).runtimes.resolve()
        target = runtime_install.runtime_dir(self.data, runtime_id)
        if not target.exists():
            return {"runtime": runtime_id, "removed": None}
        for env in (p for p in target.iterdir() if p.is_dir()):
            marker = runtime_install.read_marker(env)
            if marker is not None and runtime_install.marker_problem(env, marker) == "newer_format":
                raise InstallRefused(
                    f"The {env.name} build of {runtime_id!r} was installed by a newer version of the "
                    "app; it is left as it is.",
                    "newer_format",
                )
        if target.is_symlink() or target.resolve().parent != root or target.name != runtime_id:
            raise InstallRefused(
                f"Refusing to delete {target}: it is not a runtime folder in {root}.", "unsafe_path"
            )
        runtime_install.remove_tree(target)
        return {"runtime": runtime_id, "removed": str(target)}


def uv_available(uv: str | None) -> bool:
    return bool(uv) and (Path(str(uv)).is_file() or shutil.which(str(uv)) is not None)
