from __future__ import annotations

import ast
import json
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import Events, MakeEngine, MakeNode, StartEngine, call, wait_until
from PIL import Image

from oneframe import BUILTIN_NODES_DIR, ENGINE_DIR
from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.processes import wait_gone
from oneframe.registry import discover
from oneframe.scheduler import Scheduler
from oneframe.server import Engine, Requests


def test_importing_the_server_loads_no_numeric_or_model_library() -> None:
    probe = (
        "import sys, oneframe.server\n"
        "heavy = ('numpy', 'PIL', 'torch', 'cv2', 'transformers', 'truststore')\n"
        "heavy = [m for m in heavy if m in sys.modules]\n"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_reading_a_manifest_loads_only_the_memory_model() -> None:
    probe = (
        "import sys, oneframe.manifest\n"
        "print(','.join(sorted(m for m in sys.modules if m.startswith('oneframe.'))))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    loaded = set(out.stdout.strip().split(","))
    assert "oneframe.manifest" in loaded
    assert {m for m in loaded if m.startswith("oneframe.memory.")} == {"oneframe.memory.model"}
    assert not loaded & {"oneframe.files", "oneframe.layout", "oneframe.errors"}, sorted(loaded)


@pytest.mark.parametrize("package", ["memory", "runtimes", "models"])
def test_a_package_root_holds_only_its_docstring(package: str) -> None:
    tree = ast.parse((ENGINE_DIR / package / "__init__.py").read_text(encoding="utf-8"))
    assert ast.get_docstring(tree), f"oneframe/{package}/ says what it holds"
    assert len(tree.body) == 1, f"oneframe/{package}/ re-exports nothing: callers import its modules"


def _photo(path: Path, focal_35: int | None = 26) -> Path:
    image = Image.new("RGB", (8, 6), (200, 100, 50))
    exif = Image.Exif()
    if focal_35:
        exif.get_ifd(0x8769)[0xA405] = focal_35  # Exif IFD, FocalLengthIn35mmFilm
    image.save(path, exif=exif)
    return path


def test_the_server_answers_and_runs_a_graph(tmp_path: Path, engine_process: StartEngine) -> None:
    client = engine_process([sys.executable, "-m", "oneframe.server", "--data", str(tmp_path / "data")])
    client.event("engine.ready")
    try:
        hello = client.call("engine.hello")["result"]
        assert hello["python"].startswith("3.15") and "graph.run" in hello["methods"]
        listed = client.call("nodes.list")["result"]
        assert {n["id"] for n in listed["nodes"]} >= {"source.image", "convert.depth_to_points"}
        assert "Depth" in client.call("ports.list")["result"]["types"]
        assert "Unknown method" in client.call("nope")["error"]["message"]

        bad = client.call(
            "graph.run", {"graph": {"version": 1, "nodes": {"x": {"node": "no.such"}}, "edges": []}}
        )
        assert bad["error"]["problems"][0]["node"] == "x"

        photo = _photo(tmp_path / "p.jpg")
        imported = client.call("inputs.import", {"path": str(photo)})["result"]
        assert imported["name"] == "p.jpg"
        graph = {
            "version": 1,
            "nodes": {"p": {"node": "source.image", "params": {"path": imported["id"]}}},
            "edges": [],
        }
        run = client.call("graph.run", {"graph": graph})["result"]["run"]
        done = client.event("run.done", "run.failed")
        assert done["event"] == "run.done" and done["run"] == run
        image = done["outputs"]["p"]["image"]
        assert image["facets"] == {"alpha": "none"} and image["trust"] == "measured"
        assert image["meta"]["focal_px_exif"] == round(26 / 36 * 8, 2)
        # The import's name travels with the result; the photo's folders, and so the person's user
        # name, do not.
        assert image["meta"]["source"] == f"{imported['id']}.jpg"
        assert str(tmp_path) not in json.dumps(image["meta"])
    finally:
        client.close()


@pytest.mark.usefixtures("basic_nodes")
def test_a_graph_round_trips_over_the_protocol(
    tmp_path: Path, node_root: Path, engine_process: StartEngine
) -> None:
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    data = tmp_path / "data"
    argv = [sys.executable, "-m", "oneframe.server", "--data", str(data), "--nodes", str(node_root)]
    client = engine_process([*argv, "--graphs", str(shipped)])
    client.event("engine.ready")
    graph = {
        "version": 1,
        "title": "Mine",
        "nodes": {
            "p": {"node": "test.image", "params": {}, "page": {"at": [10, 20]}},
            "d": {"node": "test.const_depth", "params": {}},
        },
        "edges": [],
        "page": {"shown": [{"step": "d", "port": "depth"}]},
    }
    try:
        assert client.call("graphs.list")["result"] == {"graphs": [], "problems": {}}
        saved = client.call("graphs.save", {"name": "mine", "graph": graph})["result"]
        assert saved == {"name": "mine", "source": "saved", "title": "Mine"}
        assert (data / "graphs" / "mine.json").is_file()
        listed = client.call("graphs.list")["result"]["graphs"]
        assert listed == [{"name": "mine", "source": "saved", "title": "Mine"}]
        assert client.call("graphs.get", {"name": "mine"})["result"]["graph"] == graph
        # What fits here answers over the same protocol: the image feeds the depth node's input.
        fitted = client.call("graph.fits", {"graph": graph, "port": "p.image"})["result"]
        assert [(a["step"], a["port"], a["how"]) for a in fitted["graph"]] == [("d", "image", "direct")]
        removed = client.call("graphs.remove", {"name": "mine"})["result"]
        assert removed == {"name": "mine", "removed": True, "now": None}
        assert client.call("graphs.list")["result"]["graphs"] == []
        gone = client.call("graphs.get", {"name": "mine"})["error"]
        assert (gone["kind"], gone["reason"]) == ("request", "bad_params")
    finally:
        client.close()


def test_an_engine_process_that_says_nothing_fails_the_wait_by_name(engine_process: StartEngine) -> None:
    """A wait ends at its deadline while the engine is silent, naming what it waited for; once the
    engine has ended, at once."""
    silent = engine_process([sys.executable, "-c", "import sys; sys.stdin.read()"])
    with pytest.raises(AssertionError, match=r"waited 0\.5 s for engine\.ready; the engine's log is "):
        silent.event("engine.ready", seconds=0.5)
    assert silent.process.poll() is None  # still running: the fixture kills it at teardown
    assert silent.close() == 0
    with pytest.raises(AssertionError, match=r"the engine ended before engine\.ready"):
        silent.event("engine.ready", seconds=60)


def test_depth_to_points_unprojects_through_the_pinhole(tmp_path: Path) -> None:
    sched = Scheduler(
        discover([BUILTIN_NODES_DIR, tmp_path / "extra"]),
        Cache(tmp_path / "cache" / "objects", tmp_path / "cache" / "tmp"),
    )
    extra = tmp_path / "extra" / "test.depth_and_camera"
    extra.mkdir(parents=True)
    (extra / "node.json").write_text(
        json.dumps(
            {
                "id": "test.depth_and_camera",
                "version": "1",
                "title": "t",
                "category": "test",
                "outputs": {
                    "depth": "Depth[kind=metric, measure=z]",
                    "camera": "Camera[model=pinhole, source=given]",
                },
            }
        ),
        encoding="utf-8",
    )
    (extra / "node.py").write_text(
        "import json, numpy as np\n"
        "def run(ctx):\n"
        "    d = np.full((2, 4), 2.0, dtype=np.float32); d[0, 0] = 0\n"
        "    p = ctx.path('d.npz'); np.savez(p, depth=d); ctx.output('depth', p)\n"
        "    c = ctx.path('c.json'); c.write_text(json.dumps({'fx': 2, 'fy': 2, 'cx': 2, 'cy': 1}))\n"
        "    ctx.output('camera', c)\n",
        encoding="utf-8",
    )
    sched.registry = discover([BUILTIN_NODES_DIR, tmp_path / "extra"])
    graph = Graph.from_json(
        {
            "version": 1,
            "nodes": {"s": {"node": "test.depth_and_camera"}, "u": {"node": "convert.depth_to_points"}},
            "edges": [{"from": "s.depth", "to": "u.depth"}, {"from": "s.camera", "to": "u.camera"}],
        }
    )
    result = sched.run(graph, lambda e: None)
    assert result.status == "done", result.error
    data = np.load(result.outputs["u"]["points"].path)
    points, valid = data["points"], data["valid"]
    assert points.shape == (2, 4, 3)
    assert not valid[0, 0] and valid[1, 3]
    # pixel (u=3, v=1) has centre (3.5, 1.5): X = (3.5 - 2) * 2 / 2, Y = (1.5 - 1) * 2 / 2, Z = 2
    assert np.allclose(points[1, 3], [1.5, 0.5, 2.0])
    assert result.outputs["u"]["points"].facets == {"frame": "camera", "scale": "metric"}


def test_shutdown_stops_a_running_graph_and_its_node_process(
    tmp_path: Path,
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    make_engine: MakeEngine,
) -> None:
    """The app quitting closes the engine's stdin, and the engine shuts down: a node still loading
    a model must not outlive it holding the card."""
    make_node(
        {
            "id": "test.hang",
            "version": "1",
            "title": "hang",
            "category": "test",
            "outputs": {"text": "Text"},
            "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
        },
        "import os, time\ndef run(ctx):\n    ctx.stage('hang', str(os.getpid()))\n    time.sleep(60)\n",
    )
    engine = make_engine(tmp_path / "data", [tmp_path / "nodes"], events.append, runtime_roots=[])
    engine.scheduler = scheduler_for()  # runtime nodes run in this test's own Python
    graph = {"version": 1, "nodes": {"h": {"node": "test.hang"}}, "edges": []}
    reply = engine.handle({"id": 1, "method": "graph.run", "params": {"graph": graph}})
    assert reply is not None and "result" in reply, reply
    wait_until(lambda: events.of("stage"), lambda: f"the node's stage: {events}", 30)
    pid = int(events.of("stage")[0]["message"])

    engine.shutdown(timeout=20)
    assert events.of("run.stopped")
    assert wait_gone(pid)


DEPTH_RUN = {
    "version": 1,
    "nodes": {"i": {"node": "test.image"}, "d": {"node": "test.const_depth"}},
    "edges": [{"from": "i.image", "to": "d.image"}],
}


@pytest.mark.usefixtures("basic_nodes")
def test_values_are_located_only_when_committed(
    tmp_path: Path, node_root: Path, make_engine: MakeEngine, events: Events
) -> None:
    engine = make_engine(tmp_path / "data", [node_root], events.append)
    result = engine.scheduler.run(Graph.from_json(DEPTH_RUN), events.append)
    assert result.status == "done", result.error
    depth = result.outputs["d"]["depth"]
    entry = depth.key.partition(":")[0]  # a value's key is its entry's, and its port
    assert entry == next(e["key"] for e in events.of("node.done") if e["step"] == "d")

    found = call(engine, "values.locate", key=entry, port="depth")["result"]
    assert found == {"path": str(depth.path), "type": "Depth", "carrier": "npz"}
    assert Path(found["path"]).is_relative_to(engine.layout.objects)

    for params in (
        {"key": "0" * 64, "port": "depth"},  # no entry has this key
        {"key": entry, "port": "image"},  # the entry has no such port
        {"key": depth.key, "port": "depth"},  # a value's own key is not its entry's
        {"key": "../" * 8 + "etc/passwd", "port": "depth"},  # a path is no key
        {"key": entry.upper(), "port": "depth"},
        {"key": entry, "port": 3},
        {"port": "depth"},
    ):
        refused = call(engine, "values.locate", **params)["error"]
        assert (refused["kind"], refused["reason"]) == ("request", "bad_params"), params


HELD = "nodes.forget"  # a queued method, whose answer the contract declares (spec 018)


def _held(engine: Engine) -> tuple[threading.Event, threading.Event]:
    """HELD made to hold the worker until it is let go: (started, let go)."""
    started, let_go = threading.Event(), threading.Event()

    def hold(_params: dict[str, Any]) -> dict[str, Any]:
        started.set()
        assert let_go.wait(30), "the test never let the held request go"
        return {"node": None, "dropped": 0}

    engine.methods[HELD] = hold
    return started, let_go


def test_a_stop_is_answered_while_a_request_works(tmp_path: Path, make_engine: MakeEngine) -> None:
    replies: list[dict[str, Any]] = []
    engine = make_engine(tmp_path / "data", [], lambda _event: None)
    started, let_go = _held(engine)
    requests = Requests(engine, replies.append)
    try:
        requests.take({"id": 1, "method": HELD})
        assert started.wait(10), "the held request never began"
        requests.take({"id": 2, "method": "run.stop"})
        requests.take({"id": 3, "method": "engine.hello"})
        assert [r["id"] for r in replies] == [2, 3], "a stop and a hello wait for no other request"
        assert replies[0]["result"] == {"stopping": False}
    finally:
        let_go.set()
        requests.close()
    assert [r["id"] for r in replies] == [2, 3, 1]


@pytest.mark.usefixtures("basic_nodes")
def test_a_stop_cancels_a_queued_run(tmp_path: Path, node_root: Path, make_engine: MakeEngine) -> None:
    replies: list[dict[str, Any]] = []
    seen: list[dict[str, Any]] = []
    engine = make_engine(tmp_path / "data", [node_root], seen.append)
    started, let_go = _held(engine)
    requests = Requests(engine, replies.append)
    try:
        requests.take({"id": 1, "method": HELD})
        assert started.wait(10), "the held request never began"
        requests.take({"id": 2, "method": "graph.run", "params": {"graph": DEPTH_RUN}})
        requests.take({"id": 3, "method": "nodes.list"})  # not a run: it stays queued
        requests.take({"id": 4, "method": "run.stop"})
        assert replies == [
            {"id": 2, "result": {"stopped": True}},
            {"id": 4, "result": {"stopping": False}},
        ]
        let_go.set()
        wait_until(lambda: len(replies) == 4 or None, "the queued nodes.list's answer")
    finally:
        let_go.set()
        requests.close()
    assert [r["id"] for r in replies] == [2, 4, 1, 3]
    assert not [e for e in seen if e.get("event", "").startswith(("run.", "node."))], "the run never started"
