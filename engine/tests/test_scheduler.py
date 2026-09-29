from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import numpy as np
import pytest
from conftest import Events, MakeNode

from oneframe.graph import Graph
from oneframe.scheduler import Scheduler


def _graph(nodes: Mapping[str, Mapping[str, object]], edges: list[tuple[str, str]]) -> Graph:
    return Graph.from_json(
        {"version": 1, "nodes": dict(nodes), "edges": [{"from": a, "to": b} for a, b in edges]}
    )


DEPTH_GRAPH = ({"i": {"node": "test.image"}, "d": {"node": "test.const_depth"}}, [("i.image", "d.image")])


@pytest.mark.usefixtures("basic_nodes")
def test_a_run_produces_checked_values_with_trust(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    result = scheduler_for().run(_graph(*DEPTH_GRAPH), events.append)
    assert result.status == "done", result.error
    assert events.kinds() == ["run.start", "node.start", "node.done", "node.start", "node.done", "run.done"]
    depth = result.outputs["d"]["depth"]
    assert depth.facets == {"kind": "metric", "measure": "z"}
    assert depth.trust == "predicted"
    assert result.outputs["i"]["image"].trust == "measured"
    assert np.load(depth.path)["depth"].mean() == pytest.approx(2.0)
    assert all(e["run"] == result.run for e in events)


@pytest.mark.usefixtures("basic_nodes")
def test_the_second_run_comes_from_the_cache(scheduler_for: Callable[..., Scheduler], events: Events) -> None:
    sched = scheduler_for()
    first = sched.run(_graph(*DEPTH_GRAPH), events.append)
    events.clear()
    second = sched.run(_graph(*DEPTH_GRAPH), events.append)
    assert events.kinds() == ["run.start", "node.cached", "node.cached", "run.done"]
    assert second.outputs["d"]["depth"].path == first.outputs["d"]["depth"].path


@pytest.mark.usefixtures("basic_nodes")
def test_changing_one_param_reruns_only_what_follows(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    sched = scheduler_for()
    sched.run(_graph(*DEPTH_GRAPH), events.append)
    nodes, edges = DEPTH_GRAPH
    events.clear()
    sched.run(
        _graph({**nodes, "d": {"node": "test.const_depth", "params": {"z": 3.0}}}, edges), events.append
    )
    assert [(e["event"], e.get("step")) for e in events if e["event"].startswith("node.")] == [
        ("node.cached", "i"),
        ("node.start", "d"),
        ("node.done", "d"),
    ]
    events.clear()
    # A speed-only parameter does not change the result, so it does not change the key.
    sched.run(
        _graph({**nodes, "d": {"node": "test.const_depth", "params": {"z": 3.0, "batch": 8}}}, edges),
        events.append,
    )
    assert events.kinds().count("node.cached") == 2


def test_a_file_param_is_keyed_by_content_not_name(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], tmp_path: Path, events: Events
) -> None:
    make_node(
        {
            "id": "test.reads",
            "version": "1",
            "title": "R",
            "category": "test",
            "outputs": {"text": "Text"},
            "params": {"path": {"type": "file"}},
        },
        "def run(ctx):\n    p = ctx.path('t.json')\n    p.write_text('1')\n    ctx.output('text', p)\n",
    )
    src = tmp_path / "in.txt"
    src.write_text("a", encoding="utf-8")
    g = _graph({"r": {"node": "test.reads", "params": {"path": str(src)}}}, [])
    sched = scheduler_for()
    sched.run(g, events.append)
    src.write_text("b", encoding="utf-8")
    events.clear()
    sched.run(g, events.append)
    assert "node.start" in events.kinds(), "a changed file must not be served from the cache"


@pytest.mark.parametrize(
    ("code", "kind", "needle"),
    [
        ("def run(ctx):\n    pass\n", "contract", "without its output"),
        ("def run(ctx):\n    ctx.output('depth', ctx.path('missing.npz'))\n", "contract", "does not exist"),
        (
            "def run(ctx):\n    p = ctx.path('d.npz'); p.write_bytes(b'x')\n    ctx.output('depth', p)\n",
            "contract",
            "did not say its kind",
        ),
        (
            "def run(ctx):\n    p = ctx.path('d.npz'); p.write_bytes(b'x')\n"
            "    ctx.output('depth', p, facets={'kind': 'metric', 'measure': 'range'})\n",
            "contract",
            "contradicts",
        ),
        (
            "def run(ctx):\n    raise RuntimeError('CUDA out of memory. Tried to allocate')\n",
            "oom",
            "out of memory",
        ),
        ("def run(ctx):\n    import not_a_real_package\n", "missing", "not_a_real_package"),
    ],
)
def test_failures_are_classified_and_leave_nothing_cached(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    tmp_path: Path,
    code: str,
    kind: str,
    needle: str,
) -> None:
    make_node(
        {
            "id": "test.bad",
            "version": "1",
            "title": "B",
            "category": "test",
            "outputs": {"depth": "Depth[measure=z]"},
        },
        code,
    )
    result = scheduler_for().run(_graph({"b": {"node": "test.bad"}}, []), events.append)
    assert result.status == "failed"
    assert result.error is not None and result.error["kind"] == kind
    assert needle in result.error["message"]
    assert events.kinds()[-2:] == ["node.failed", "run.failed"]
    assert not list((tmp_path / "cache" / "objects").glob("*/*")), "a failed run must not leave a cache entry"
    assert not list((tmp_path / "cache" / "tmp").glob("*")), "nor its working folder"


def test_an_open_facet_is_checked_when_the_value_arrives(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    make_node(
        {
            "id": "test.either",
            "version": "1",
            "title": "E",
            "category": "test",
            "outputs": {"depth": "Depth[measure=z]"},
            "params": {
                "kind": {"type": "choice", "choices": ["metric", "scale_invariant"], "default": "metric"}
            },
        },
        "def run(ctx):\n    p = ctx.path('d.npz'); p.write_bytes(b'x')\n"
        "    ctx.output('depth', p, facets={'kind': ctx.params['kind']})\n",
    )
    make_node(
        {
            "id": "test.metric_only",
            "version": "1",
            "title": "M",
            "category": "test",
            "inputs": {"depth": "Depth[kind=metric]"},
            "outputs": {"text": "Text"},
        },
        "def run(ctx):\n    p = ctx.path('t.json'); p.write_text('1')\n    ctx.output('text', p)\n",
    )
    ok = scheduler_for().run(
        _graph({"e": {"node": "test.either"}, "m": {"node": "test.metric_only"}}, [("e.depth", "m.depth")]),
        events.append,
    )
    assert ok.status == "done"
    bad = scheduler_for().run(
        _graph(
            {
                "e": {"node": "test.either", "params": {"kind": "scale_invariant"}},
                "m": {"node": "test.metric_only"},
            },
            [("e.depth", "m.depth")],
        ),
        events.append,
    )
    assert bad.status == "failed" and bad.error is not None
    assert "needs metric" in bad.error["message"]


def test_trust_is_inherited_as_the_weakest_input(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    for name, trust in (("test.real", "measured"), ("test.dreamt", "synthetic")):
        make_node(
            {
                "id": name,
                "version": "1",
                "title": name,
                "category": "test",
                "outputs": {"text": {"type": "Text", "trust": trust}},
            },
            "def run(ctx):\n    p = ctx.path('t.json'); p.write_text('1')\n    ctx.output('text', p)\n",
        )
    make_node(
        {
            "id": "test.join",
            "version": "1",
            "title": "J",
            "category": "test",
            "inputs": {"a": "Text", "b": "Text"},
            "outputs": {"text": "Text"},
        },
        "def run(ctx):\n    p = ctx.path('t.json'); p.write_text('1')\n    ctx.output('text', p)\n",
    )
    result = scheduler_for().run(
        _graph(
            {"r": {"node": "test.real"}, "s": {"node": "test.dreamt"}, "j": {"node": "test.join"}},
            [("r.text", "j.a"), ("s.text", "j.b")],
        ),
        events.append,
    )
    assert result.outputs["j"]["text"].trust == "synthetic"


def test_stop_ends_the_run_between_and_inside_nodes(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    make_node(
        {"id": "test.slow", "version": "1", "title": "S", "category": "test", "outputs": {"text": "Text"}},
        "import time\ndef run(ctx):\n    for i in range(200):\n        ctx.check_stop()\n"
        "        ctx.progress(i, 200)\n        time.sleep(0.01)\n",
    )
    calls = {"n": 0}

    def stop_after_a_few() -> bool:
        calls["n"] += 1
        return calls["n"] > 5

    result = scheduler_for().run(_graph({"s": {"node": "test.slow"}}, []), events.append, stop_after_a_few)
    assert result.status == "stopped"
    assert events.kinds()[-1] == "run.stopped"
    assert 0 < len(events.of("progress")) < 200
