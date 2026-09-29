"""Nodes that run in a child process: the protocol survives what model code does to stdout, the
network stays closed, failures keep their kind, and Stop kills the child."""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest
from conftest import Events, MakeNode

from oneframe.graph import Graph
from oneframe.scheduler import RuntimeMissing, Scheduler


def _runtime_node(make_node: MakeNode, node_id: str, code: str, **extra: object) -> None:
    manifest: dict[str, object] = {
        "id": node_id,
        "version": "1",
        "title": node_id,
        "category": "test",
        "outputs": {"text": "Text"},
        "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
    }
    manifest.update(extra)
    make_node(manifest, code)


def _one(node_id: str) -> Graph:
    return Graph.from_json({"version": 1, "nodes": {"n": {"node": node_id}}, "edges": []})


WRITE = "    p = ctx.path('t.json'); p.write_text('1'); ctx.output('text', p)\n"


def test_printing_code_cannot_corrupt_the_protocol(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.noisy",
        "import os, sys\n"
        "def run(ctx):\n"
        '    print(\'{"event": "done", "fake": true}\')\n'
        "    os.write(1, b'raw bytes on fd 1\\n')\n"
        "    sys.stdout.write('more noise\\n')\n"
        "    ctx.stage('work', 'doing it')\n"
        "    for i in range(3):\n"
        "        ctx.progress(i + 1, 3)\n" + WRITE,
    )
    result = scheduler_for().run(_one("test.noisy"), events.append)
    assert result.status == "done", result.error
    assert [e["done"] for e in events.of("progress")] == [1, 2, 3]
    assert events.of("stage")[0]["message"] == "doing it"
    assert all(e.get("step") == "n" for e in events.of("progress"))


def test_the_network_is_closed_during_a_run(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.net",
        "import socket\ndef run(ctx):\n    socket.create_connection(('203.0.113.7', 443), timeout=2)\n"
        + WRITE,
    )
    result = scheduler_for().run(_one("test.net"), events.append)
    assert result.status == "failed" and result.error is not None
    assert result.error["kind"] == "fetch"
    assert "203.0.113.7" in result.error["message"]


def test_a_child_that_dies_is_reported_with_its_stderr(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.die",
        "import os, sys\ndef run(ctx):\n    sys.stderr.write('last words\\n'); sys.stderr.flush()\n"
        "    os._exit(3)\n",
    )
    result = scheduler_for().run(_one("test.die"), events.append)
    assert result.error is not None and result.error["kind"] == "died"
    assert "code 3" in result.error["message"]
    assert "last words" in result.error["detail"]


def test_stop_kills_a_runtime_child(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node, "test.hang", "import time\ndef run(ctx):\n    ctx.stage('hang')\n    time.sleep(60)\n"
    )
    started = time.monotonic()

    def stop_when_hanging() -> bool:
        return any(e["event"] == "stage" for e in events)

    result = scheduler_for().run(_one("test.hang"), events.append, stop_when_hanging)
    assert result.status == "stopped"
    assert time.monotonic() - started < 20


def test_a_missing_runtime_is_its_own_failure(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(make_node, "test.needs_runtime", "def run(ctx):\n" + WRITE)

    def none(runtime: str) -> object:
        raise RuntimeMissing(f"The runtime {runtime!r} is not installed.")

    result = scheduler_for(runtime_python=none).run(_one("test.needs_runtime"), events.append)
    assert result.error is not None and result.error["kind"] == "runtime"


@pytest.mark.usefixtures("basic_nodes")
def test_engine_and_runtime_nodes_mix_in_one_graph(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.remote_depth",
        "import numpy as np\n"
        "def run(ctx):\n"
        "    size = len(ctx.inputs['image'].path.read_bytes())\n"
        "    p = ctx.path('d.npz'); np.savez(p, depth=np.ones((2, 2)) * size)\n"
        "    ctx.output('depth', p, facets={'kind': 'metric'})\n",
        inputs={"image": "Image"},
        outputs={"depth": "Depth[measure=z]"},
    )
    g = Graph.from_json(
        {
            "version": 1,
            "nodes": {"i": {"node": "test.image"}, "r": {"node": "test.remote_depth"}},
            "edges": [{"from": "i.image", "to": "r.image"}],
        }
    )
    result = scheduler_for().run(g, events.append)
    assert result.status == "done", result.error
    assert result.outputs["r"]["depth"].facets == {"kind": "metric", "measure": "z"}
