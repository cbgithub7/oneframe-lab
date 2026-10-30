"""Test nodes are written on the fly into a temporary folder, each a real node.json plus node.py,
so every test goes through the same discovery, validation and execution a real node does.

Runtime tests install real environments with the real uv: the tiny runtime (two small builds of a
pure-Python package) from its committed lock, with uv's cache and Pythons shared by the whole
session so Python 3.11 is fetched once. They need the network (PyPI, and uv's Python downloads);
offline they fail with the download error rather than skip."""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fixtures.hosts import Hosts
from fixtures.store import sha

from oneframe.cache import Cache
from oneframe.files import is_free
from oneframe.memory.estimate import Target
from oneframe.registry import Registry, discover
from oneframe.scheduler import Scheduler
from oneframe.server import Engine

# The test guard's collection hooks (spec 007), and pytester for their tests.
pytest_plugins = ("pytester", "fixtures.listing")

MakeNode = Callable[..., Path]

# A node that turns an image into a constant depth plane and a pinhole camera.
CONST_DEPTH = {
    "manifest": {
        "id": "test.const_depth",
        "version": "1",
        "title": "Constant depth",
        "category": "test",
        "inputs": {"image": "Image"},
        "outputs": {
            "depth": {"type": "Depth[kind=metric, measure=z]", "trust": "predicted"},
            "camera": {"type": "Camera[model=pinhole, source=estimated]", "trust": "predicted"},
        },
        "params": {
            "z": {"type": "float", "default": 2.0, "min": 0.1},
            "batch": {"type": "int", "default": 1, "min": 1, "affects": "speed"},
        },
    },
    "code": """
        import json
        import numpy as np

        CALLS = []

        def run(ctx):
            CALLS.append(ctx.params)
            h, w = 4, 6
            path = ctx.path("depth.npz")
            np.savez(path, depth=np.full((h, w), ctx.params["z"], dtype=np.float32))
            ctx.output("depth", path)
            cam = ctx.path("camera.json")
            cam.write_text(json.dumps({"fx": 3.0, "fy": 3.0, "cx": 3.0, "cy": 2.0, "width": w, "height": h}))
            ctx.output("camera", cam)
    """,
}

# A source with no inputs that writes a tiny image; its content depends on a param.
FAKE_IMAGE = {
    "manifest": {
        "id": "test.image",
        "version": "1",
        "title": "Fake image",
        "category": "test",
        "outputs": {"image": {"type": "Image[alpha=none]", "trust": "measured"}},
        "params": {"seed": {"type": "int", "default": 0}},
    },
    "code": """
        def run(ctx):
            p = ctx.path("image.bin")
            p.write_bytes(bytes([ctx.params["seed"] % 256]) * 16)
            ctx.output("image", p)
    """,
}


def _write(root: Path, manifest: dict[str, Any], code: str) -> Path:
    folder = root / manifest["id"]
    folder.mkdir(parents=True, exist_ok=True)
    manifest = dict(manifest)
    manifest.setdefault("run", {"where": "engine", "entry": "node.py:run"})
    (folder / "node.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "node.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return folder


def wait_until[T](check: Callable[[], T | None], what: str | Callable[[], str], seconds: float = 10) -> T:
    """Wait until `check` gives something true, and give that back. Past `seconds` the test fails,
    naming what it waited for (`what`, or what it says then)."""
    deadline = time.monotonic() + seconds
    while not (found := check()):
        assert time.monotonic() < deadline, f"waited {seconds:g} s for {what() if callable(what) else what}"
        time.sleep(0.02)
    return found


def wait_free(*paths: Path, seconds: float = 30) -> None:
    """Wait until nobody holds these locks. Windows lets go of a dead process's locks a little after
    it has ended ("depends upon available system resources", LockFileEx), so a test that ends a
    holder waits for its locks rather than assuming them free at once."""
    wait_until(lambda: all(is_free(p) for p in paths), "the locks of a holder that ended", seconds)


# A lock another process holds: it takes the lock, writes a file if asked (a run's reading list,
# say), says "held" and its pid, and holds the lock until told to let go, or is killed.
HOLDER = textwrap.dedent(
    """
    import os, sys
    from pathlib import Path
    from oneframe.files import FileLock
    lock = FileLock(Path(sys.argv[1]))
    assert lock.acquire()
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(sys.argv[3], encoding="utf-8")
    print("held", os.getpid(), flush=True)
    sys.stdin.readline()
    lock.release()
    print("released", flush=True)
    """
)


class Holder:
    """The process holding a lock for `held_elsewhere`."""

    def __init__(self, path: Path, write: tuple[Path, str] | None = None) -> None:
        more = [str(write[0]), write[1]] if write else []
        self.process = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(path), *more],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            encoding="utf-8",
        )
        self.pid = 0  # the interpreter's own, once it has said it holds the lock
        try:
            assert self.process.stdout is not None
            said = self.process.stdout.readline().split()
            assert said[:1] == ["held"], f"the holder said {said}"
            self.pid = int(said[1])
        except BaseException:
            self.kill()
            raise

    def let_go(self) -> None:
        """Release the lock and end, as a holder that finishes does. Windows frees a killed
        holder's lock only later; one that lets go frees it at once."""
        stdin, stdout = self.process.stdin, self.process.stdout
        assert stdin is not None and stdout is not None
        if self.process.poll() is None and not stdin.closed:
            stdin.write("\n")
            stdin.close()
            assert stdout.readline().strip() == "released"
        self._end()

    def kill(self) -> None:
        """End it holding the lock, as a crash would; the system lets go of the lock (see
        wait_free)."""
        # The holder itself as well as the process started: on Windows a venv's python.exe is a
        # launcher, and the interpreter behind it, which holds the lock, may outlive it a moment.
        # Only while the launcher lives, whose handle keeps the holder's pid from being reused.
        if self.process.poll() is None and self.pid:
            with contextlib.suppress(OSError):
                os.kill(self.pid, signal.SIGTERM)
        self.process.kill()
        self._end()

    def _end(self) -> None:
        self.process.wait(timeout=30)
        for pipe in (self.process.stdin, self.process.stdout):
            if pipe is not None:
                with contextlib.suppress(OSError):
                    pipe.close()


@contextlib.contextmanager
def held_elsewhere(path: Path, write: tuple[Path, str] | None = None) -> Iterator[Holder]:
    """The lock at `path`, held by another process for the block, which may let it go or kill it;
    leaving the block lets go if it still holds it."""
    holder = Holder(path, write)
    try:
        yield holder
    finally:
        holder.let_go()


@pytest.fixture
def stop_after() -> Iterator[Callable[[float], threading.Event]]:
    """A Stop that sets itself `seconds` from now, for a test that would hang if what it checks came
    back: the run is stopped and the test fails instead. Its timer cannot outlive the test."""
    timers: list[threading.Timer] = []

    def make(seconds: float) -> threading.Event:
        stop = threading.Event()
        timer = threading.Timer(seconds, stop.set)
        timer.daemon = True
        timer.start()
        timers.append(timer)
        return stop

    yield make
    for timer in timers:
        timer.cancel()


@pytest.fixture
def node_root(tmp_path: Path) -> Path:
    root = tmp_path / "nodes"
    root.mkdir()
    return root


@pytest.fixture
def make_node(node_root: Path) -> MakeNode:
    def make(manifest: dict[str, Any], code: str = "def run(ctx):\n    pass\n") -> Path:
        return _write(node_root, manifest, code)

    return make


@pytest.fixture
def basic_nodes(make_node: MakeNode) -> None:
    make_node(FAKE_IMAGE["manifest"], FAKE_IMAGE["code"])
    make_node(CONST_DEPTH["manifest"], CONST_DEPTH["code"])


@pytest.fixture
def registry_for(node_root: Path) -> Callable[[], Registry]:
    return lambda: discover([node_root])


class HostRuntimes:
    """A runtimes double (runtimes.manager.RuntimeSide): every runtime is the engine's own Python, with the
    variables, the fit's target and the cache key a test sets, and no folder for library caches."""

    def __init__(
        self,
        target: Target | None = None,
        key: dict[str, str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self.target = target or Target()
        self.key = key
        self.env = dict(env or {})

    def python_for(self, runtime_id: str) -> Path:
        return Path(sys.executable)

    def env_for(self, runtime_id: str) -> dict[str, str]:
        return dict(self.env)

    def caches_for(self, runtime_id: str) -> Path | None:
        return None

    def key_for(self, runtime_id: str) -> dict[str, str] | None:
        return None if self.key is None else dict(self.key)

    def device_target(self, runtime_id: str) -> Target:
        return self.target


@pytest.fixture
def host_runtimes() -> HostRuntimes:
    return HostRuntimes()


@pytest.fixture
def scheduler_for(tmp_path: Path, node_root: Path, host_runtimes: HostRuntimes) -> Callable[..., Scheduler]:
    """A scheduler for the test's nodes, its runtimes `host_runtimes` unless the test gives others."""

    def make(**kw: Any) -> Scheduler:
        kw.setdefault("runtimes", host_runtimes)
        return Scheduler(
            discover([node_root]), Cache(tmp_path / "cache" / "objects", tmp_path / "cache" / "tmp"), **kw
        )

    return make


TINY_RUNTIME = Path(__file__).parent / "runtimes" / "tiny"
MakeRuntime = Callable[..., Path]


def write_runtime(
    root: Path,
    definition: dict[str, Any],
    lock: str = 'version = 1\nrequires-python = "==3.11.*"\n',
    extras: list[str] | None = None,
    package: bool = False,
) -> Path:
    """A runtime folder for tests that plan or check definitions: runtime.json as given, a
    pyproject with an extra per build (all in one conflict set) and the lock text as given."""
    folder = root / definition["id"]
    folder.mkdir(parents=True, exist_ok=True)
    names = extras if extras is not None else [b["name"] for b in definition.get("builds") or []]
    conflicts = ", ".join(f'{{ extra = "{n}" }}' for n in names)
    pyproject = (
        f'[project]\nname = "rt-{definition["id"]}"\nversion = "1"\nrequires-python = ">=3.11"\n'
        "[project.optional-dependencies]\n"
        + "".join(f"{n} = []\n" for n in names)
        + f"[tool.uv]\npackage = {'true' if package else 'false'}\n"
        + (f"conflicts = [[{conflicts}]]\n" if len(names) > 1 else "")
    )
    (folder / "pyproject.toml").write_text(pyproject, encoding="utf-8", newline="\n")
    (folder / "uv.lock").write_text(lock, encoding="utf-8", newline="\n")
    (folder / "runtime.json").write_text(json.dumps(definition, indent=2), encoding="utf-8", newline="\n")
    return folder


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtimes"
    root.mkdir()
    return root


@pytest.fixture
def make_runtime(runtime_root: Path) -> MakeRuntime:
    def make(definition: dict[str, Any], **kw: Any) -> Path:
        return write_runtime(runtime_root, definition, **kw)

    return make


class Events(list[dict[str, Any]]):
    def kinds(self) -> list[str]:
        return [e["event"] for e in self]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self if e["event"] == kind]


@pytest.fixture
def events() -> Events:
    return Events()


# -- installing runtimes -------------------------------------------------------------------------

NO_GPU: dict[str, Any] = {
    "os": "linux",
    "gpus": [],
    "driver": None,
    "nvidia": {"found": False, "why": "nvidia-smi was not found, so no NVIDIA card is known."},
    "disk_free_mb": 10**6,
    "raw": "",
}
AMPERE: dict[str, Any] = {
    **NO_GPU,
    "gpus": [
        {"index": 0, "vendor": "nvidia", "name": "test card", "capability": "8.6", "vram_total_mb": 12885}
    ],
    "driver": "580.88",
    "nvidia": {"found": True, "why": "nvidia-smi listed 1 card."},
}


@pytest.fixture(scope="session")
def uv_exe() -> str:
    found = os.environ.get("UV") or shutil.which("uv")
    assert found, "the runtime tests need uv; run them with `uv run` (npm run engine:check)"
    return found


@pytest.fixture(scope="session")
def uv_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """uv's cache and Pythons for the whole session, so each test does not fetch them again."""
    return tmp_path_factory.mktemp("uv-home")


@pytest.fixture
def tiny(tmp_path: Path) -> Path:
    """A copy of the tiny runtime, which a test may change without touching the repo's."""
    folder = tmp_path / "defs" / "tiny"
    shutil.copytree(TINY_RUNTIME, folder)
    return folder


def upstream_archive(value: str = "upstream") -> bytes:
    """A GitHub-style source archive: one top folder holding the package upstream_pkg."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        data = f"WHERE = {value!r}\n".encode()
        info = tarfile.TarInfo("upstream-0123abc/upstream_pkg/__init__.py")
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def add_source(folder: Path, url: str, data: bytes, name: str = "upstream") -> None:
    path = folder / "runtime.json"
    definition = json.loads(path.read_text(encoding="utf-8"))
    definition["sources"] = [{"name": name, "url": url, "sha256": sha(data), "paths": ["."]}]
    path.write_text(json.dumps(definition, indent=2), encoding="utf-8")


@pytest.fixture
def hosts() -> Iterator[Hosts]:
    """Spec 003's hosts (fixtures/hosts.py): the hub, a files host, a cdn and a proxy, over TLS."""
    found = Hosts()
    try:
        yield found
    finally:
        found.close()


@pytest.fixture
def hub(hosts: Hosts, monkeypatch: pytest.MonkeyPatch) -> Hosts:
    """The test hosts, with the hub client pointed at their imitation of Hugging Face."""
    monkeypatch.setenv("HF_ENDPOINT", hosts.hub)
    return hosts


def call(engine: Engine, method: str, **params: Any) -> dict[str, Any]:
    """One request to an in-process engine, as the page sends it; its reply."""
    return engine.handle({"id": 1, "method": method, "params": params}) or {}
