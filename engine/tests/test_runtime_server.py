"""The runtime methods over the engine's NDJSON server: list, plan, install (with its events), stop
and remove; an engine killed in the middle of an install; and listing and planning with the network
shut (AC4, AC6)."""

from __future__ import annotations

import json
import queue
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import NO_GPU, Events, MakeNode, SourceServer, add_source, upstream_archive
from fixtures import fit_node, machines

from oneframe import BUILTIN_NODES_DIR, runtime_install
from oneframe.server import Engine

WAIT_S = 180  # the first install in a session fetches Python 3.11


class Server:
    """The engine as the app runs it: a process, requests on stdin, answers and events on stdout."""

    def __init__(self, data: Path, runtime_root: Path, uv: str, uv_home: Path, log: Path):
        self.log = log.open("a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "oneframe.server",
                "--data",
                str(data),
                "--runtimes",
                str(runtime_root),
                "--nodes",
                str(BUILTIN_NODES_DIR),
                "--uv",
                uv,
                "--uv-home",
                str(uv_home),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            encoding="utf-8",
        )
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.seen: list[dict[str, Any]] = []
        self.next_id = 0
        threading.Thread(target=self._read, daemon=True).start()
        self.wait(lambda m: m.get("event") == "engine.ready", "engine.ready")

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if line.strip():
                self.messages.put(json.loads(line))

    def wait(
        self, match: Callable[[dict[str, Any]], bool], what: str, timeout: float = WAIT_S
    ) -> dict[str, Any]:
        for message in self.seen:
            if match(message):
                self.seen.remove(message)
                return message
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            try:
                message = self.messages.get(timeout=0.5)
            except queue.Empty:
                continue
            if match(message):
                return message
            self.seen.append(message)
        raise TimeoutError(f"{what} did not arrive; the engine's log is {self.log.name}")

    def event(self, *kinds: str, **fields: Any) -> dict[str, Any]:
        return self.wait(
            lambda m: m.get("event") in kinds and all(m.get(k) == v for k, v in fields.items()),
            "/".join(kinds),
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.next_id += 1
        ident = self.next_id
        self.proc.stdin.write(json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n")
        self.proc.stdin.flush()
        return self.wait(lambda m: m.get("id") == ident, method)

    def kill(self) -> None:
        self.proc.kill()
        self.proc.wait(timeout=30)
        self.log.close()

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        self.proc.wait(timeout=30)
        self.log.close()


def _stalled_source(tiny: Path, server: SourceServer) -> threading.Event:
    """Give the tiny runtime a source whose download stops halfway until the returned event is set."""
    archive = upstream_archive()
    server.files["/up.tar.gz"] = archive
    release = threading.Event()
    server.stalled["/up.tar.gz"] = release
    add_source(tiny, server.url("/up.tar.gz"), archive)
    return release


def test_the_runtime_methods_work_over_the_server(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    data = tmp_path / "data"
    server = Server(data, tiny.parent, uv_exe, uv_home, tmp_path / "engine.log")
    try:
        hello = server.call("engine.hello")["result"]
        assert {
            "runtimes.list",
            "runtimes.plan",
            "runtimes.install",
            "runtimes.stop",
            "runtimes.remove",
        } <= set(hello["methods"])
        [row] = server.call("runtimes.list")["result"]["runtimes"]
        assert row["id"] == "tiny" and row["status"] == "not installed"
        build = row["plan"]["build"]
        assert build in ("cpu", "cu130")  # whatever this machine runs

        plans = server.call("runtimes.plan", {"runtime": "tiny", "refresh": True})["result"]
        assert plans["plans"][0]["build"] == build and "raw" not in plans["profile"]
        assert (
            server.call("runtimes.plan", {"runtime": "nope"})["error"]["message"]
            == "No runtime called 'nope' is defined."
        )
        assert server.call("runtimes.stop", {"runtime": "tiny"})["result"] == {"stopping": False}

        assert server.call("runtimes.install", {"runtime": "tiny"})["result"] == {
            "runtime": "tiny",
            "build": build,
        }
        server.event("runtime.start", runtime="tiny")
        done = server.event("runtime.done", "runtime.failed", "runtime.stopped", runtime="tiny")
        assert done["event"] == "runtime.done", done
        assert server.call("runtimes.list")["result"]["runtimes"][0]["status"] == "installed"
        assert server.call("runtimes.install", {"runtime": "tiny"})["result"] == {
            "runtime": "tiny",
            "already": True,
        }

        removed = server.call("runtimes.remove", {"runtime": "tiny"})["result"]
        assert removed == {"runtime": "tiny", "removed": str(data / "runtimes" / "tiny")}
        assert server.call("runtimes.list")["result"]["runtimes"][0]["status"] == "not installed"
    finally:
        server.close()


def test_a_killed_install_is_not_installed_and_a_second_install_completes(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, source_server: SourceServer
) -> None:
    release = _stalled_source(tiny, source_server)
    data = tmp_path / "data"
    server = Server(data, tiny.parent, uv_exe, uv_home, tmp_path / "engine.log")
    build = server.call("runtimes.install", {"runtime": "tiny"})["result"]["build"]
    # uv sync has finished and the source is half downloaded: the marker cannot have been written
    server.event("runtime.progress", runtime="tiny", step="sources")
    server.kill()

    env = data / "runtimes" / "tiny" / build
    assert (env / "pyvenv.cfg").is_file()
    assert not (env / runtime_install.MARKER).exists()

    del source_server.stalled["/up.tar.gz"]
    release.set()
    again = Server(data, tiny.parent, uv_exe, uv_home, tmp_path / "engine.log")
    try:
        assert again.call("runtimes.list")["result"]["runtimes"][0]["status"] == "not installed"
        again.call("runtimes.install", {"runtime": "tiny"})
        done = again.event("runtime.done", "runtime.failed", "runtime.stopped", runtime="tiny")
        assert done["event"] == "runtime.done", done
        assert again.call("runtimes.list")["result"]["runtimes"][0]["status"] == "installed"
        assert (env / runtime_install.MARKER).is_file()
    finally:
        again.close()


def test_stop_ends_an_install_and_installs_run_one_at_a_time(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, source_server: SourceServer
) -> None:
    release = _stalled_source(tiny, source_server)
    data = tmp_path / "data"
    server = Server(data, tiny.parent, uv_exe, uv_home, tmp_path / "engine.log")
    try:
        build = server.call("runtimes.install", {"runtime": "tiny"})["result"]["build"]
        server.event("runtime.progress", runtime="tiny", step="sources")

        second = server.call("runtimes.install", {"runtime": "tiny"})
        assert second["error"]["message"] == "tiny is being installed; runtimes are installed one at a time."
        removing = server.call("runtimes.remove", {"runtime": "tiny"})
        assert removing["error"]["message"] == "tiny is being installed; stop the install before removing it."
        assert server.call("runtimes.list")["result"]["runtimes"][0]["status"] == "installing"

        assert server.call("runtimes.stop", {"runtime": "tiny"})["result"] == {"stopping": True}
        release.set()  # the download notices Stop when its next bytes arrive
        ended = server.event("runtime.done", "runtime.failed", "runtime.stopped", runtime="tiny")
        assert ended["event"] == "runtime.stopped", ended
        assert not (data / "runtimes" / "tiny" / build / runtime_install.MARKER).exists()
        assert server.call("runtimes.list")["result"]["runtimes"][0]["status"] == "not installed"
    finally:
        server.close()


def test_listing_and_planning_touch_no_network(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(tmp_path / "data", [BUILTIN_NODES_DIR], Events().append, [tiny.parent], uv_exe, uv_home)
    assert engine.runtimes.install("tiny") is not None  # the network is open for the install
    engine.runtimes._profile = None  # read the machine again, under watch

    connects: list[Any] = []
    started: list[str] = []

    def refuse(*args: Any, **_kw: Any) -> Any:
        connects.append(args)
        raise OSError("the network is closed for this test")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    real_popen = subprocess.Popen

    class Watched(real_popen):  # type: ignore[misc, valid-type]
        def __init__(self, args: Any, *rest: Any, **kw: Any) -> None:
            started.append(Path(str(args[0] if isinstance(args, list | tuple) else args)).stem.lower())
            super().__init__(args, *rest, **kw)

    monkeypatch.setattr(subprocess, "Popen", Watched)

    listed = engine.handle({"id": 1, "method": "runtimes.list"})
    planned = engine.handle({"id": 2, "method": "runtimes.plan", "params": {"refresh": True}})
    one = engine.handle({"id": 3, "method": "runtimes.plan", "params": {"runtime": "tiny"}})

    assert listed is not None and listed["result"]["runtimes"][0]["status"] == "installed"
    assert planned is not None and "result" in planned and one is not None and "result" in one
    assert connects == []
    assert set(started) <= {"nvidia-smi"}, started


def test_a_runtime_a_run_is_using_is_neither_installed_over_nor_removed(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, make_node: MakeNode, node_root: Path
) -> None:
    make_node(
        {
            "id": "test.waits_in_tiny",
            "version": "1",
            "title": "Waits",
            "category": "test",
            "outputs": {"text": "Text"},
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
        },
        "import time\ndef run(ctx):\n    ctx.stage('wait')\n    time.sleep(120)\n",
    )
    events = Events()
    engine = Engine(
        tmp_path / "data", [node_root], events.append, [tiny.parent], uv_exe, uv_home, lambda: NO_GPU
    )
    assert engine.runtimes.install("tiny") is not None
    graph = {"version": 1, "nodes": {"w": {"node": "test.waits_in_tiny"}}, "edges": []}
    reply = engine.handle({"id": 1, "method": "graph.run", "params": {"graph": graph}})
    assert reply is not None
    run = reply["result"]["run"]
    end = time.monotonic() + WAIT_S
    while not events.of("stage") and not events.of("run.failed") and time.monotonic() < end:
        time.sleep(0.1)
    assert events.of("stage"), events

    removing = engine.handle({"id": 2, "method": "runtimes.remove", "params": {"runtime": "tiny"}})
    installing = engine.handle({"id": 3, "method": "runtimes.install", "params": {"runtime": "tiny"}})
    assert removing is not None and installing is not None
    assert removing["error"]["message"] == f"Run {run} is using tiny; stop it before removing the runtime."
    assert (
        installing["error"]["message"] == f"Run {run} is using tiny; stop it before installing the runtime."
    )

    engine.handle({"id": 4, "method": "run.stop", "params": {}})
    while not events.of("run.stopped") and time.monotonic() < end:
        time.sleep(0.1)
    assert events.of("run.stopped")
    while engine._run is not None and time.monotonic() < end:
        time.sleep(0.05)
    removed = engine.handle({"id": 5, "method": "runtimes.remove", "params": {"runtime": "tiny"}})
    assert removed is not None and removed["result"]["removed"] is not None


def test_an_engine_that_shuts_down_stops_its_install(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, source_server: SourceServer
) -> None:
    """The app closes the engine's stdin when it quits; an install still going must not outlive it."""
    release = _stalled_source(tiny, source_server)
    events = Events()
    engine = Engine(
        tmp_path / "data", [BUILTIN_NODES_DIR], events.append, [tiny.parent], uv_exe, uv_home, lambda: NO_GPU
    )
    reply = engine.handle({"id": 1, "method": "runtimes.install", "params": {"runtime": "tiny"}})
    assert reply is not None and "result" in reply
    end = time.monotonic() + WAIT_S
    while not events.of("runtime.progress") and not events.of("runtime.failed") and time.monotonic() < end:
        time.sleep(0.05)
    assert events.of("runtime.progress"), events

    closing = threading.Thread(target=engine.shutdown)
    closing.start()
    release.set()  # the download sees Stop when its next bytes arrive
    closing.join(60)
    assert not closing.is_alive()
    assert events.of("runtime.stopped") and engine.runtimes.installing() is None
    assert not (tmp_path / "data" / "runtimes" / "tiny" / "cpu" / runtime_install.MARKER).exists()


# -- spec 002 in the tiny runtime: the retry (AC3) and what a run sees (AC7) ---------------------

# The tiny runtime's cu130 build on a compute 7.5 card. Its system memory is small enough that the
# processor cannot fit the test node, so a retry stays on the card and takes the next change.
CARD_7_5 = machines.with_system(machines.get("6 GB, compute 7.5"), 5000, 8590)

FIT_SEEN = """
import json, os, weakref

class OutOfMemoryError(RuntimeError):
    pass

def run(ctx):
{extra}
    p = ctx.path("seen.json")
    p.write_text(json.dumps({{
        "params": ctx.params, "device": ctx.device, "attempt": ctx.attempt, "pid": os.getpid(),
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES"), "tiny": os.environ.get("ONEFRAME_TINY"),
    }}))
    ctx.output("text", p)
"""


def _tiny_node(
    make_node: MakeNode,
    node_id: str,
    extra: str = "    pass",
    *,
    model: bool = True,
    devices: tuple[str, ...] = ("cuda", "cpu"),
) -> None:
    manifest: dict[str, Any] = {
        "id": node_id,
        "version": "1",
        "title": node_id,
        "category": "test",
        "outputs": {"text": "Text"},
        "params": fit_node.PARAMS,
        "devices": list(devices),
        "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
    }
    if model:
        manifest["memory"] = fit_node.model()
    make_node(manifest, FIT_SEEN.format(extra=extra))


def _run_graph(engine: Engine, events: Events, node: str, **params: Any) -> list[dict[str, Any]]:
    """Run one node through the engine's own method; the events of that run."""
    engine.handle({"id": 0, "method": "nodes.reload"})  # the test wrote its node after the engine started
    graph = {"version": 1, "nodes": {"n": {"node": node, "params": params}}, "edges": []}
    reply = engine.handle({"id": 1, "method": "graph.run", "params": {"graph": graph}})
    assert reply is not None and "result" in reply, reply
    run = reply["result"]["run"]
    end = time.monotonic() + WAIT_S
    while time.monotonic() < end:
        ended = [
            e
            for e in events
            if e.get("run") == run and e["event"] in ("run.done", "run.failed", "run.stopped")
        ]
        if ended and engine._run is None:
            return [e for e in events if e.get("run") == run]
        time.sleep(0.05)
    raise TimeoutError(f"run {run} did not end")


@pytest.fixture
def tiny_engine(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, node_root: Path
) -> tuple[Engine, Events]:
    events = Events()
    engine = Engine(
        tmp_path / "data", [node_root], events.append, [tiny.parent], uv_exe, uv_home, lambda: CARD_7_5
    )
    installed = engine.runtimes.install("tiny")
    assert installed is not None and installed["build"] == "cu130", installed
    return engine, events


def _kinds(run: list[dict[str, Any]]) -> list[str]:
    return [e["event"] for e in run if e["event"].startswith("node.")]


def test_ac3_out_of_memory_is_retried_once_in_a_new_process_with_the_next_change(
    make_node: MakeNode, tiny_engine: tuple[Engine, Events]
) -> None:
    _tiny_node(
        make_node,
        "test.tiny_fit",
        "    if ctx.attempt == 1:\n        raise OutOfMemoryError('CUDA out of memory')",
    )
    engine, events = tiny_engine
    run = _run_graph(engine, events, "test.tiny_fit")
    assert _kinds(run) == ["node.fit", "node.start", "node.oom", "node.fit", "node.start", "node.done"], run
    first, second = [e for e in run if e["event"] == "node.fit"]
    assert first["device"] == second["device"] == "cuda"
    assert [c["change"] for c in first["changes"]] == [0, 1]
    assert [c["change"] for c in second["changes"]] == [0, 1, 2]  # the next change: fp16
    assert [e["attempt"] for e in run if e["event"] == "node.start"] == [1, 2]
    # AC7: the tiny runtime has no torch, so a card fit with a cap runs uncapped, and says so.
    ceilings = [e for e in run if e["event"] == "ceiling"]
    assert [c["applied"] for c in ceilings] == [False, False] and ceilings[0]["budget_mb"] == 4389
    done = next(e for e in run if e["event"] == "node.done")
    assert done["made_with"]["precision"] == "fp16" and done["made_with"]["reduced"] is True
    seen = json.loads(Path(done["outputs"]["text"]["path"]).read_text(encoding="utf-8"))
    assert seen["attempt"] == 2 and seen["tiny"] == "on" and seen["device"] == "cuda"


def test_ac3_a_second_oom_another_failure_or_stop_ends_the_node(
    make_node: MakeNode, tiny_engine: tuple[Engine, Events]
) -> None:
    engine, events = tiny_engine
    _tiny_node(make_node, "test.tiny_oom", "    raise OutOfMemoryError('CUDA out of memory')")
    _tiny_node(make_node, "test.tiny_bad", "    raise ValueError('expected a square image')")
    _tiny_node(make_node, "test.tiny_wait", "    import time\n    ctx.stage('wait')\n    time.sleep(120)")
    engine.handle({"id": 0, "method": "nodes.reload"})

    twice = _run_graph(engine, events, "test.tiny_oom")
    failed = next(e for e in twice if e["event"] == "node.failed")
    assert failed["kind"] == "oom" and failed["message"].startswith("Ran out of memory twice")
    assert len(failed["fits"]) == 2 and [p["attempt"] for p in failed["peaks"]] == [1, 2]
    assert not list((engine.data / "cache" / "objects").glob("*/*/outputs.json"))  # nothing cached

    other = _run_graph(engine, events, "test.tiny_bad")
    assert _kinds(other) == ["node.fit", "node.start", "node.failed"]

    graph = {"version": 1, "nodes": {"n": {"node": "test.tiny_wait"}}, "edges": []}
    reply = engine.handle({"id": 2, "method": "graph.run", "params": {"graph": graph}})
    assert reply is not None
    run_id = reply["result"]["run"]
    end = time.monotonic() + WAIT_S
    while (
        not [e for e in events if e.get("run") == run_id and e["event"] == "stage"] and time.monotonic() < end
    ):
        time.sleep(0.05)
    engine.handle({"id": 3, "method": "run.stop", "params": {}})
    while engine._run is not None and time.monotonic() < end:
        time.sleep(0.05)
    stopped = [e for e in events if e.get("run") == run_id]
    assert stopped[-1]["event"] == "run.stopped" and _kinds(stopped) == ["node.fit", "node.start"]


def test_ac3_fallbacks_move_to_the_next_way_in_the_same_process_after_releasing_the_first(
    make_node: MakeNode, tiny_engine: tuple[Engine, Events]
) -> None:
    extra = (
        "    held = []\n"
        "    class Tensor:\n"
        "        pass\n"
        "    def whole():\n"
        "        big = Tensor()\n"
        "        held.append(weakref.ref(big))\n"
        "        raise OutOfMemoryError('CUDA out of memory')\n"
        "    released = ctx.fallbacks('decode', [('whole', whole), ('tiled', lambda: held[0]() is None)])\n"
        "    if not released:\n"
        "        raise RuntimeError('the failed way was still held')"
    )
    _tiny_node(make_node, "test.tiny_ways", extra)
    engine, events = tiny_engine
    run = _run_graph(engine, events, "test.tiny_ways")
    assert _kinds(run) == ["node.fit", "node.start", "node.step_oom", "node.done"], run
    moved = next(e for e in run if e["event"] == "node.step_oom")
    assert (moved["stage"], moved["way"], moved["next"]) == ("decode", "whole", "tiled")
    assert next(e for e in run if e["event"] == "node.done")["made_with"]["ways"] == {"decode": "tiled"}


def test_ac7_a_processor_run_in_a_gpu_build_sees_no_card(
    make_node: MakeNode, tiny_engine: tuple[Engine, Events]
) -> None:
    _tiny_node(make_node, "test.tiny_cpu", model=False, devices=("cpu",))
    engine, events = tiny_engine
    run = _run_graph(engine, events, "test.tiny_cpu")
    done = next(e for e in run if e["event"] == "node.done")
    seen = json.loads(Path(done["outputs"]["text"]["path"]).read_text(encoding="utf-8"))
    assert seen["device"] == "cpu" and seen["cuda_visible"] == "-1"
    assert not [e for e in run if e["event"] == "ceiling"]


def test_ac7_nodes_fit_and_forget_answer_without_the_network(
    make_node: MakeNode, tiny_engine: tuple[Engine, Events], monkeypatch: pytest.MonkeyPatch
) -> None:
    _tiny_node(make_node, "test.tiny_fit")
    engine, events = tiny_engine
    engine.handle({"id": 0, "method": "nodes.reload"})
    _run_graph(engine, events, "test.tiny_fit")  # something learned, to forget

    connects: list[Any] = []

    def refuse(*args: Any, **_kw: Any) -> Any:
        connects.append(args)
        raise OSError("the network is closed for this test")

    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)

    fitted = engine.handle({"id": 1, "method": "nodes.fit", "params": {"node": "test.tiny_fit"}})
    assert fitted is not None and "result" in fitted, fitted
    assert fitted["result"]["device"] == "cuda" and [c["change"] for c in fitted["result"]["changes"]] == [
        0,
        1,
    ]
    explicit = engine.handle(
        {"id": 2, "method": "nodes.fit", "params": {"node": "test.tiny_fit", "params": {"chunk_size": 8192}}}
    )
    assert explicit is not None and explicit["result"]["outcome"] == "fits"
    assert {"change": 0, "why": "the graph sets chunk_size"} in explicit["result"]["skipped"]
    wrong = engine.handle(
        {"id": 3, "method": "nodes.fit", "params": {"node": "test.tiny_fit", "params": {"steps": 3}}}
    )
    assert wrong is not None and "has no parameter steps" in wrong["error"]["message"]
    forgot = engine.handle({"id": 4, "method": "nodes.forget", "params": {"node": "test.tiny_fit"}})
    assert forgot is not None and forgot["result"]["dropped"] >= 1
    assert connects == []


def test_ac7_nodes_fit_and_forget_load_no_model_library(
    tmp_path: Path, node_root: Path, make_node: MakeNode
) -> None:
    make_node(
        {
            "id": "test.fit_engine",
            "version": "1",
            "title": "Fit",
            "category": "test",
            "outputs": {"text": "Text"},
            "params": fit_node.PARAMS,
            "devices": ["cuda", "cpu"],
            "memory": fit_node.model(),
        }
    )
    probe = (
        "import json, sys\n"
        "from pathlib import Path\n"
        "from oneframe.server import Engine\n"
        f"engine = Engine(Path({str(tmp_path / 'data')!r}), [Path({str(node_root)!r})], lambda e: None)\n"
        "a = engine.handle({'id': 1, 'method': 'nodes.fit', 'params': {'node': 'test.fit_engine'}})\n"
        "b = engine.handle({'id': 2, 'method': 'nodes.forget', 'params': {'node': 'test.fit_engine'}})\n"
        "heavy = [m for m in ('numpy', 'PIL', 'torch', 'cv2', 'transformers') if m in sys.modules]\n"
        "print(json.dumps({'fit': 'result' in a, 'forget': 'result' in b, 'heavy': heavy}))"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True, timeout=120
    )
    assert json.loads(out.stdout.strip().splitlines()[-1]) == {"fit": True, "forget": True, "heavy": []}


def test_nodes_fit_refuses_what_a_graph_would_refuse(
    tmp_path: Path, make_node: MakeNode, node_root: Path
) -> None:
    make_node(
        {
            "id": "test.needs_value",
            "version": "1",
            "title": "Needs a value",
            "category": "test",
            "outputs": {"text": "Text"},
            "params": {**fit_node.PARAMS, "prompt": {"type": "string"}},
            "devices": ["cuda", "cpu"],
            "memory": fit_node.model(),
        }
    )
    engine = Engine(tmp_path / "data", [node_root], Events().append)
    missing = engine.handle({"id": 1, "method": "nodes.fit", "params": {"node": "test.needs_value"}})
    assert missing is not None and "parameter prompt needs a value" in missing["error"]["message"]
    given = engine.handle(
        {
            "id": 2,
            "method": "nodes.fit",
            "params": {"node": "test.needs_value", "params": {"prompt": "a chair"}},
        }
    )
    assert given is not None and "result" in given, given
