from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import (
    HOLD_CODE,
    MASKS,
    PER_MASK,
    TAKE_MASKS,
    Events,
    Gates,
    HostRuntimes,
    MakeNode,
    hold_manifest,
    wait_until,
)
from fixtures import fit_node, machines

from oneframe.cache import Cache
from oneframe.errors import Stopped
from oneframe.executors import Executor, OneProcessPerRun
from oneframe.graph import Graph, plan
from oneframe.inputs import import_file
from oneframe.layout import Layout
from oneframe.manifest import Manifest
from oneframe.memory.estimate import Learned, Target
from oneframe.memory.learned import LearnedStore, StoreKey
from oneframe.memory.settings import Settings
from oneframe.node import Emit
from oneframe.node.job import Done, Job
from oneframe.runtimes.manager import NoRuntimes
from oneframe.scheduler import Scheduler, _meta
from oneframe.slots import Place, Slot, Slots


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
def test_a_run_up_to_a_step_runs_only_what_it_needs(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Spec 022, AC1: in a graph of two branches from one source, a run up to one branch's last step
    says that branch's steps as its order, ends done with their outputs, and never names the other."""
    graph = _graph(
        {"i": {"node": "test.image"}, "a": {"node": "test.const_depth"}, "b": {"node": "test.const_depth"}},
        [("i.image", "a.image"), ("i.image", "b.image")],
    )
    result = scheduler_for().run(graph, events.append, upto=["a"])
    assert result.status == "done", result.error
    assert events.of("run.start")[0]["order"] == ["i", "a"]
    assert set(result.outputs) == {"i", "a"}
    assert set(events.of("run.done")[0]["outputs"]) == {"i", "a"}
    assert all(event.get("step") != "b" for event in events), "the other branch is never named"


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
            "category": "evaluate",
            "outputs": {"text": "Text"},
            "params": {"path": {"type": "file"}},
        },
        "def run(ctx):\n    p = ctx.path('t.json')\n    p.write_text('1')\n    ctx.output('text', p)\n",
    )
    src = tmp_path / "in.txt"
    src.write_text("a", encoding="utf-8")
    layout = Layout(tmp_path)  # where scheduler_for finds imports
    reads = lambda ident: _graph({"r": {"node": "test.reads", "params": {"path": ident}}}, [])  # noqa: E731
    sched = scheduler_for()
    sched.run(reads(import_file(layout, src)[0]), events.append)
    src.write_text("b", encoding="utf-8")
    events.clear()
    sched.run(reads(import_file(layout, src)[0]), events.append)
    assert "node.start" in events.kinds(), "a changed file must not be served from the cache"
    copy = tmp_path / "another name.txt"
    copy.write_text("b", encoding="utf-8")
    events.clear()
    sched.run(reads(import_file(layout, copy)[0]), events.append)
    assert events.kinds() == ["run.start", "node.cached", "run.done"], "the same bytes, by another name"


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
            # In the test's cache folder: outside the run, and nothing left in the system's temp.
            "def run(ctx):\n"
            "    p = ctx.path('d.npz').parents[2] / 'outside.npz'; p.write_bytes(b'x')\n"
            "    ctx.output('depth', p, facets={'kind': 'metric'})\n",
            "contract",
            "outside the run folder",
        ),
        (
            "def run(ctx):\n    raise RuntimeError('CUDA out of memory. Tried to allocate')\n",
            "oom",
            "out of memory",
        ),
        ("def run(ctx):\n    import not_a_real_package\n", "missing", "not_a_real_package"),
    ],
    ids=[
        "no output",
        "a missing file",
        "no kind",
        "contradicting facets",
        "outside the run folder",
        "out of memory",
        "a missing package",
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
            "category": "evaluate",
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


@pytest.mark.usefixtures("basic_nodes")
def test_a_failure_stops_only_what_depends_on_it(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Two branches from one photo: one fails, so the step after it never starts, and the other
    runs to the end. The run then ends failed, naming the step that failed and the one skipped."""
    make_node(
        {
            "id": "test.fails",
            "version": "1",
            "title": "F",
            "category": "evaluate",
            "inputs": {"image": "Image"},
            "outputs": {"depth": "Depth[kind=metric, measure=z]"},
        },
        "def run(ctx):\n    raise RuntimeError('this branch fails')\n",
    )
    make_node(
        {
            "id": "test.after",
            "version": "1",
            "title": "A",
            "category": "evaluate",
            "inputs": {"depth": "Depth"},
            "outputs": {"depth": "Depth[kind=metric, measure=z]"},
        },
        "def run(ctx):\n    raise AssertionError('never started')\n",
    )
    graph = _graph(
        {
            "i": {"node": "test.image"},
            "bad": {"node": "test.fails"},
            "after": {"node": "test.after"},
            "d": {"node": "test.const_depth"},
        },
        [("i.image", "bad.image"), ("bad.depth", "after.depth"), ("i.image", "d.image")],
    )
    result = scheduler_for().run(graph, events.append)
    assert result.status == "failed"
    assert (result.failed, result.skipped) == (["bad"], ["after"])
    assert set(result.outputs) == {"i", "d"}, "the other branch ran to the end"
    assert [e["step"] for e in events.of("node.start")] == ["i", "bad", "d"]
    assert [e["step"] for e in events.of("node.done")] == ["i", "d"]
    [failed] = events.of("node.failed")
    assert failed["step"] == "bad" and "this branch fails" in failed["message"]
    [skipped] = events.of("node.skipped")
    assert skipped == {
        "event": "node.skipped",
        "step": "after",
        "node": "test.after",
        "waited_on": "bad",
        "run": result.run,
    }
    ending = events[-1]
    assert ending["event"] == "run.failed" and ending["step"] == "bad"
    assert (ending["failed"], ending["skipped"]) == (["bad"], ["after"])


def test_an_open_facet_is_checked_when_the_value_arrives(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    make_node(
        {
            "id": "test.either",
            "version": "1",
            "title": "E",
            "category": "evaluate",
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
            "category": "evaluate",
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
                "category": "evaluate",
                "outputs": {"text": {"type": "Text", "trust": trust}},
            },
            "def run(ctx):\n    p = ctx.path('t.json'); p.write_text('1')\n    ctx.output('text', p)\n",
        )
    make_node(
        {
            "id": "test.join",
            "version": "1",
            "title": "J",
            "category": "evaluate",
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


def test_a_value_names_the_sources_it_descends_from(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Every output names the values no step made from another that it comes from (spec 019, AC8);
    a node cannot set them."""
    writes = (
        "def run(ctx):\n    p = ctx.path('t.json'); p.write_text('1')\n"
        "    ctx.output('text', p, meta={'sources': []})\n"
    )
    for name, inputs in (
        ("test.photo", {}),
        ("test.prompt", {}),  # another node, so another entry: two sources, not one served twice
        ("test.one", {"a": "Text"}),
        ("test.two", {"a": "Text", "b": "Text"}),
    ):
        make_node(
            {
                "id": name,
                "version": "1",
                "title": name,
                "category": "evaluate",
                "inputs": inputs,
                "outputs": {"text": "Text"},
            },
            writes,
        )
    nodes = {
        "r": {"node": "test.photo"},
        "s": {"node": "test.prompt"},
        "one": {"node": "test.one"},
        "both": {"node": "test.two"},
    }
    result = scheduler_for().run(
        _graph(nodes, [("r.text", "one.a"), ("one.text", "both.a"), ("s.text", "both.b")]), events.append
    )
    assert result.status == "done", result.error
    r, s = result.outputs["r"]["text"], result.outputs["s"]["text"]
    assert r.meta["sources"] == [{"id": r.key, "type": "Text"}]  # a source names itself
    assert result.outputs["one"]["text"].meta["sources"] == [{"id": r.key, "type": "Text"}]
    both = sorted([{"id": r.key, "type": "Text"}, {"id": s.key, "type": "Text"}], key=lambda row: row["id"])
    assert result.outputs["both"]["text"].meta["sources"] == both


def test_a_json_value_is_held_to_its_shape_where_it_is_made(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """A node whose Camera does not fit Camera's shape breaks its contract, naming the field (spec
    020, AC3)."""
    make_node(
        {
            "id": "test.camera",
            "version": "1",
            "title": "Camera",
            "category": "evaluate",
            "outputs": {"camera": "Camera[model=pinhole, source=estimated]"},
        },
        "import json\n"
        "def run(ctx):\n"
        "    p = ctx.path('c.json')\n"
        "    p.write_text(json.dumps({'fy': 2, 'cx': 2, 'cy': 1, 'width': 4, 'height': 2}))\n"
        "    ctx.output('camera', p)\n",
    )
    result = scheduler_for().run(_graph({"c": {"node": "test.camera"}}, []), events.append)
    assert result.status == "failed"
    [failed] = events.of("node.failed")
    assert failed["kind"] == "contract"
    assert "test.camera.camera does not fit Camera's shape: camera: no field fx" in failed["message"]


def test_stop_ends_the_run_between_and_inside_nodes(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    make_node(
        {
            "id": "test.slow",
            "version": "1",
            "title": "S",
            "category": "evaluate",
            "outputs": {"text": "Text"},
        },
        "import time\ndef run(ctx):\n    for i in range(200):\n        ctx.check_stop()\n"
        "        ctx.progress(i, 200)\n        time.sleep(0.01)\n",
    )
    stop = threading.Event()

    def listen(event: dict[str, Any]) -> None:
        events.append(event)
        if len(events.of("progress")) >= 4:
            stop.set()

    result = scheduler_for().run(_graph({"s": {"node": "test.slow"}}, []), listen, stop)
    assert result.status == "stopped"
    assert events.kinds()[-1] == "run.stopped"
    assert 0 < len(events.of("progress")) < 200


# -- spec 002: fitting each step to memory -------------------------------------------------------

# Machines for a node that runs on the processor. The test node needs 6297 MB at its defaults,
# 4761 and 4377 after its speed-only changes, and 3590 at resolution 512 (fp16 cannot run there).
ROOMY = machines.machine([], 10000, 17180)  # budget 8282: the defaults
TIGHT = machines.machine([], 6000, 8590)  # budget 4389: chunk size 512, speed only
SMALL = machines.machine([], 5500, 8590)  # budget 3889: resolution 512 too, reduced
TINY = machines.machine([], 3000, 4295)  # budget 1389: nothing fits

SEEN = """
import json, os

class OutOfMemoryError(RuntimeError):
    pass

def run(ctx):
{extra}
    p = ctx.path("seen.json")
    p.write_text(json.dumps({{
        "params": ctx.params, "device": ctx.device, "attempt": ctx.attempt,
        "budget": ctx.memory_budget_mb, "precision": ctx.precision,
        "cuda_visible": os.environ.get("CUDA_VISIBLE_DEVICES"),
    }}))
    ctx.output("text", p)
"""
RAISE_OOM = "raise OutOfMemoryError('CUDA out of memory. Tried to allocate 2.00 GiB')"
OOM_FIRST = f"    if ctx.attempt == 1:\n        {RAISE_OOM}"
OOM_ALWAYS = f"    {RAISE_OOM}"


def _fit_node(
    make_node: MakeNode,
    node_id: str,
    extra: str = "    pass",
    *,
    where: str = "engine",
    model: bool = True,
    devices: tuple[str, ...] = ("cuda", "cpu"),
) -> None:
    manifest: dict[str, object] = {
        "id": node_id,
        "version": "1",
        "title": node_id,
        "category": "evaluate",
        "outputs": {"text": "Text"},
        "params": fit_node.PARAMS,
        "devices": list(devices),
        "run": {
            "where": where,
            "entry": "node.py:run",
            **({"runtime": "test"} if where == "runtime" else {}),
        },
    }
    if model:
        manifest["memory"] = fit_node.model()
    make_node(manifest, SEEN.format(extra=extra))


def _with(
    machine: dict[str, object], *, cards: list[bool] | None = None
) -> Callable[[bool], dict[str, object]]:
    def read(can_use_cards: bool) -> dict[str, object]:
        if cards is not None:
            cards.append(can_use_cards)
        return machines.copy.deepcopy(machine)

    return read


def _one_node(node_id: str, **params: object) -> Graph:
    return Graph.from_json({"version": 1, "nodes": {"n": {"node": node_id, "params": params}}, "edges": []})


def _seen(result: Any, step: str = "n") -> dict[str, Any]:
    return json.loads(result.outputs[step]["text"].path.read_text(encoding="utf-8"))


def test_a_fitted_engine_node_runs_at_the_fitted_values_and_says_how_it_was_made(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), events.append)
    assert result.status == "done", result.error
    assert events.kinds() == ["run.start", "node.fit", "node.start", "node.done", "run.done"]
    (fitted,) = events.of("node.fit")
    assert (fitted["attempt"], fitted["device"], fitted["outcome"]) == (1, "cpu", "fits")
    assert [c["change"] for c in fitted["changes"]] == [0, 1] and fitted["settings"]["fit"] == "on"
    seen = _seen(result)
    assert seen["params"]["chunk_size"] == 512 and seen["params"]["resolution"] == 1024
    assert seen["device"] == "cpu" and seen["attempt"] == 1 and seen["budget"] == pytest.approx(4389)
    made_with = {
        "device": "cpu",
        "precision": "fp32",
        "changed": {"chunk_size": 512},
        "ways": {},
        "reduced": False,
    }
    assert result.outputs["n"]["text"].meta["made_with"] == made_with
    (done,) = events.of("node.done")
    assert done["made_with"] == made_with and done["fit"]["outcome"] == "fits" and done["attempt"] == 1


def test_ac5_a_cached_result_at_the_steps_own_values_is_served_where_the_fit_would_reduce(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    scheduler_for(machine=_with(ROOMY)).run(_one_node("test.fit"), lambda _e: None)
    result = scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit"), events.append)
    assert events.kinds() == ["run.start", "node.cached", "run.done"]
    assert _seen(result)["params"]["resolution"] == 1024


def test_ac5_a_reduced_result_is_not_served_where_better_can_be_made(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    reduced = scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit"), lambda _e: None)
    assert _seen(reduced)["params"]["resolution"] == 512
    better = scheduler_for(machine=_with(ROOMY)).run(_one_node("test.fit"), events.append)
    assert "node.start" in events.kinds() and _seen(better)["params"]["resolution"] == 1024


def test_ac5_a_reduced_result_is_not_served_where_the_graph_sets_what_it_changed(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit"), lambda _e: None)
    result = scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit", resolution=1024), events.append)
    assert "node.start" in events.kinds()
    assert events.of("node.fit")[0]["outcome"] == "tried_anyway"
    assert _seen(result)["params"]["resolution"] == 1024


def test_ac5_a_reduced_result_is_stored_under_what_it_ran_with_and_served_down_to_the_fit(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    first = scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit"), lambda _e: None)
    record = first.records["n"]
    assert record["made_with"]["changed"] == {"chunk_size": 512, "resolution": 512}
    assert record["made_with"]["reduced"] is True
    scheduler = scheduler_for(machine=_with(SMALL))
    manifest = scheduler.registry.get("test.fit")
    defaults = {name: p.default for name, p in manifest.params.items()}
    assert record["key"] == scheduler.cache.key(
        manifest, {**defaults, "chunk_size": 512, "resolution": 512}, {}
    )
    assert record["key"] != scheduler.cache.key(manifest, defaults, {})
    scheduler.run(_one_node("test.fit"), events.append)
    assert events.kinds() == ["run.start", "node.fit", "node.cached", "run.done"]
    assert (
        events.of("node.cached")[0]["key"] == record["key"] and events.of("node.cached")[0]["fit"]["reduced"]
    )


def test_ac5_a_result_made_from_a_reduced_one_says_so(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    _fit_node(make_node, "test.fit")
    make_node(
        {
            "id": "test.after",
            "version": "1",
            "title": "After",
            "category": "evaluate",
            "inputs": {"text": "Text"},
            "outputs": {"text": "Text"},
        },
        "def run(ctx):\n    p = ctx.path('t.txt'); p.write_text('x'); ctx.output('text', p)\n",
    )
    graph = Graph.from_json(
        {
            "version": 1,
            "nodes": {"n": {"node": "test.fit"}, "a": {"node": "test.after"}},
            "edges": [{"from": "n.text", "to": "a.text"}],
        }
    )
    result = scheduler_for(machine=_with(SMALL)).run(graph, lambda _e: None)
    after = result.outputs["a"]["text"].meta
    assert after["reduced_input"] is True and after["made_with"]["reduced"] is False
    roomy = scheduler_for(machine=_with(ROOMY)).run(graph, lambda _e: None)
    assert "reduced_input" not in roomy.outputs["a"]["text"].meta


def test_ac5_precision_and_checkpoint_are_in_the_key_and_the_device_is_not(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    scheduler = scheduler_for()
    manifest = scheduler.registry.get("test.fit")
    defaults = {name: p.default for name, p in manifest.params.items()}
    assert scheduler.cache.key(manifest, defaults, {}) != scheduler.cache.key(
        manifest, {**defaults, "precision": "fp16"}, {}
    )
    model = fit_node.model()
    model["weights_by"] = "checkpoint"
    model["weights"] = {
        "small": {"fp32": 1000, "fp16": 500, "bf16": 500},
        "large": {"fp32": 3000, "fp16": 1500, "bf16": 1500},
        "source": fit_node.SOURCE,
    }
    make_node(
        {
            "id": "test.checkpoints",
            "version": "1",
            "title": "Checkpoints",
            "category": "evaluate",
            "outputs": {"text": "Text"},
            "params": {
                **fit_node.PARAMS,
                "checkpoint": {"type": "choice", "choices": ["small", "large"], "default": "large"},
            },
            "devices": ["cuda", "cpu"],
            "memory": model,
        },
        SEEN.format(extra="    pass"),
    )
    with_checkpoints = scheduler_for().registry.get("test.checkpoints")
    base = {name: p.default for name, p in with_checkpoints.params.items()}
    assert scheduler.cache.key(with_checkpoints, base, {}) != scheduler.cache.key(
        with_checkpoints, {**base, "checkpoint": "small"}, {}
    )
    # The same values on a card and then on the processor: one result.
    _fit_node(make_node, "test.fit_rt", where="runtime")
    card = machines.get("24 GB, compute 8.9")
    on_card = scheduler_for(machine=_with(card), runtimes=HostRuntimes(target=Target(card=0, lock="l"))).run(
        _one_node("test.fit_rt"), lambda _e: None
    )
    assert on_card.status == "done", on_card.error
    assert _seen(on_card, "n")["device"] == "cuda"
    scheduler_for(machine=_with(ROOMY)).run(_one_node("test.fit_rt"), events.append)
    assert events.kinds() == ["run.start", "node.cached", "run.done"]


# -- the one retry (the scheduler's half of AC3) -------------------------------------------------


def test_an_engine_node_that_runs_out_is_retried_once_in_the_engine_with_a_smaller_fit(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", OOM_FIRST)
    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), events.append)
    assert result.status == "done", result.error
    assert events.kinds() == [
        "run.start",
        "node.fit",
        "node.start",
        "node.oom",
        "node.fit",
        "node.start",
        "node.done",
        "run.done",
    ]
    first, second = events.of("node.fit")
    assert (first["attempt"], second["attempt"]) == (1, 2)
    assert [c["change"] for c in first["changes"]] == [0, 1]
    assert [c["change"] for c in second["changes"]] == [0, 1, 3]  # strictly smaller: on to resolution 512
    assert [e["attempt"] for e in events.of("node.start")] == [1, 2]
    assert events.of("node.oom")[0]["attempt"] == 1 and "out of memory" in events.of("node.oom")[0]["message"]
    seen = _seen(result)
    assert seen["attempt"] == 2 and seen["params"]["resolution"] == 512
    assert result.outputs["n"]["text"].meta["made_with"]["reduced"] is True


def test_a_second_oom_fails_with_both_fits_and_both_peaks_and_caches_nothing(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", OOM_ALWAYS, where="runtime")
    scheduler = scheduler_for(machine=_with(TIGHT))
    result = scheduler.run(_one_node("test.fit"), events.append)
    assert result.status == "failed" and result.error is not None
    assert result.error["kind"] == "oom"
    assert result.error["message"].startswith(
        "Ran out of memory twice: first on cpu with chunk_size 8192 → 2048"
    )
    assert len(result.error["fits"]) == 2 and [p["attempt"] for p in result.error["peaks"]] == [1, 2]
    assert all("peak_ram_mb" in p for p in result.error["peaks"])
    assert [e["attempt"] for e in events.of("node.oom")] == [1, 2]
    for fit in result.error["fits"]:
        assert (
            scheduler.cache.get(scheduler.cache.key(scheduler.registry.get("test.fit"), fit["values"], {}))
            is None
        )


def test_another_kind_of_failure_is_not_retried(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", "    raise ValueError('expected a square image')")
    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), events.append)
    assert result.error is not None and result.error["kind"] == "error"
    assert events.kinds().count("node.start") == 1 and not events.of("node.oom")
    assert result.error["fits"][0]["outcome"] == "fits"  # node.failed carries the fit


@pytest.mark.parametrize(
    ("node", "settings", "machine", "params"),
    [
        ("no model", Settings(), ROOMY, {}),
        ("model", Settings(fit="off"), TIGHT, {}),
        ("model", Settings(), TINY, {"resolution": 1024}),  # tried anyway
    ],
    ids=["no model", "the fit off", "nothing fits"],
)
def test_nothing_is_retried_when_nothing_would_change(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    node: str,
    settings: Settings,
    machine: dict[str, object],
    params: dict[str, object],
) -> None:
    _fit_node(make_node, "test.fit", OOM_ALWAYS, where="runtime", model=node == "model")
    result = scheduler_for(machine=_with(machine), settings=lambda: settings).run(
        _one_node("test.fit", **params), events.append
    )
    assert result.error is not None and result.error["kind"] == "oom"
    assert events.kinds().count("node.start") == 1 and len(events.of("node.oom")) == 1


def test_stop_is_never_retried(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", "    ctx.check_stop()")
    stop = threading.Event()

    def listen(event: dict[str, Any]) -> None:
        events.append(event)
        if event["event"] == "node.start":
            stop.set()  # not before the step; at the node's own check

    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), listen, stop)
    assert result.status == "stopped" and events.kinds().count("node.start") == 1


def test_nothing_fits_is_a_memory_failure_before_anything_loads(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit")
    result = scheduler_for(machine=_with(TINY)).run(_one_node("test.fit"), events.append)
    assert result.error is not None and result.error["kind"] == "memory"
    assert "Needs 5201 MB free in system memory for the processor" in result.error["message"]
    assert "node.start" not in events.kinds()


def test_a_runtime_node_that_runs_out_is_retried_in_a_new_process(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", "    ctx.stage('pid', str(os.getpid()))\n" + OOM_FIRST, where="runtime")
    result = scheduler_for(
        machine=_with(machines.get("8 GB, compute 6.1, 1.5 GB held")),
        runtimes=HostRuntimes(target=Target(card=0, lock="l")),
    ).run(_one_node("test.fit"), events.append)
    assert result.status == "done", result.error
    assert len({e["message"] for e in events.of("stage") if e["stage"] == "pid"}) == 2
    first, second = events.of("node.fit")
    assert first["device"] == second["device"] == "cuda"
    assert [c["change"] for c in first["changes"]] == [0] and [c["change"] for c in second["changes"]] == [
        0,
        1,
    ]
    # The engine's own Python has no torch: each attempt runs uncapped and says so.
    assert [e["applied"] for e in events.of("ceiling")] == [False, False]
    assert _seen(result)["params"]["chunk_size"] == 512


def test_a_step_that_falls_back_inside_the_node_is_reported_with_its_way(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    extra = (
        "    def whole():\n"
        "        raise OutOfMemoryError('out of memory')\n"
        "    ctx.fallbacks('decode', [('whole', whole), ('tiled', lambda: None)])"
    )
    _fit_node(make_node, "test.fit", extra, where="runtime")
    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), events.append)
    assert result.status == "done", result.error
    (moved,) = events.of("node.step_oom")
    assert (moved["step"], moved["stage"], moved["way"], moved["next"]) == ("n", "decode", "whole", "tiled")
    assert result.outputs["n"]["text"].meta["made_with"]["ways"] == {"decode": "tiled"}
    assert events.kinds().count("node.start") == 1


def test_ac1_a_runtime_node_without_a_model_runs_at_its_defaults_on_the_first_device_here_within_a_budget(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.plain", where="runtime", model=False)
    result = scheduler_for(machine=_with(ROOMY)).run(_one_node("test.plain"), events.append)
    assert result.status == "done", result.error
    (fitted,) = events.of("node.fit")
    assert (fitted["outcome"], fitted["device"], fitted["estimate"]) == ("unfitted", "cpu", "unknown")
    seen = _seen(result)
    assert seen["device"] == "cpu" and seen["budget"] == pytest.approx(8282)
    assert seen["params"] == {"resolution": 1024, "chunk_size": 8192}
    assert seen["cuda_visible"] == "-1"  # a processor run sees no card


def test_the_scheduler_runs_a_node_through_the_executors_it_is_given(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Spec 004's worker pool is another `Executors`: the scheduler asks it where each attempt runs
    and hands that executor the job, with no runtime installed here."""
    make_node(
        {
            "id": "test.elsewhere",
            "version": "1",
            "title": "Elsewhere",
            "category": "evaluate",
            "outputs": {"text": "Text"},
            "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
        },
        "def run(ctx):\n    raise RuntimeError('the executor given runs this job, not this code')\n",
    )
    asked: list[tuple[str, str]] = []
    jobs: list[Job] = []

    class Given:
        def executor(self, manifest: Manifest, device: str) -> Executor:
            asked.append((manifest.id, device))
            return Fake()

    class Fake:
        def execute(self, job: Job, emit: Emit, stop: threading.Event) -> Done:
            jobs.append(job)
            made = job.out_dir / "text.json"
            made.write_text('"from the executor given"', encoding="utf-8")
            return Done(outputs={"text": {"path": str(made)}}, seconds=0.5, peaks={})

    result = scheduler_for(runtimes=NoRuntimes(), executors=Given()).run(
        _one_node("test.elsewhere"), events.append
    )
    assert result.status == "done", result.error
    assert asked == [("test.elsewhere", "cpu")]
    (job,) = jobs
    assert (job.node, job.step, job.run) == ("test.elsewhere", "n", result.run)
    (done,) = events.of("node.done")
    assert done["seconds"] == 0.5
    assert Path(done["outputs"]["text"]["path"]).read_text(encoding="utf-8") == '"from the executor given"'


def test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    """A job carries no environment: the provider that starts its process hides the card for a
    processor run, and leaves the runtime's own choice alone for a card run."""
    _fit_node(make_node, "test.where", where="runtime", model=False)
    seen = []
    for machine, card, resolution in (
        (machines.machine([machines.CARD_24GB], 50000, 68719), 0, 700),
        (ROOMY, None, 701),
    ):
        runtimes = HostRuntimes(target=Target(card=card, lock="l"), env={"CUDA_VISIBLE_DEVICES": "0"})
        result = scheduler_for(
            machine=_with(machine), runtimes=runtimes, executors=OneProcessPerRun(runtimes)
        ).run(_one_node("test.where", resolution=resolution), lambda _e: None)
        assert result.status == "done", result.error
        seen.append((_seen(result)["device"], _seen(result)["cuda_visible"]))
    assert seen == [("cuda", "0"), ("cpu", "-1")]


def test_nvidia_smi_is_read_only_for_a_runtime_node_that_can_use_a_card(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    _fit_node(make_node, "test.engine")
    _fit_node(make_node, "test.cpu_only", where="runtime", devices=("cpu",), model=False)
    _fit_node(make_node, "test.card", where="runtime")
    for resolution, (node, card, expected) in enumerate(
        (
            ("test.engine", 0, [False]),
            ("test.cpu_only", 0, [False]),
            ("test.card", 0, [True]),
            ("test.card", None, [False]),  # its runtime's build has no card
        ),
        start=700,
    ):
        cards: list[bool] = []
        scheduler_for(
            machine=_with(ROOMY, cards=cards), runtimes=HostRuntimes(target=Target(card=card, lock="l"))
        ).run(_one_node(node, resolution=resolution), lambda _e: None)
        assert cards == expected, node


def test_a_runtime_node_teaches_this_machine_and_an_engine_node_does_not(
    tmp_path: Path, make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    _fit_node(make_node, "test.engine")
    _fit_node(make_node, "test.runtime", where="runtime")
    store = LearnedStore(tmp_path / "data")
    for node in ("test.engine", "test.runtime"):
        scheduler_for(
            machine=_with(ROOMY), learned=store, runtimes=HostRuntimes(target=Target(lock="l"))
        ).run(_one_node(node), lambda _e: None)
    assert store.view(StoreKey("test.engine", "1", _hash("test.engine", scheduler_for), "")) == Learned()
    learned = store.view(StoreKey("test.runtime", "1", _hash("test.runtime", scheduler_for), "l"))
    assert [kind for kind, _settings in learned.seconds] == ["cpu"]
    assert [kind for kind, _settings in learned.peaks] == ["cpu"]


def _hash(node: str, scheduler_for: Callable[..., Scheduler]) -> str:
    model = scheduler_for().registry.get(node).memory
    assert model is not None
    return model.hash


def test_an_attempt_that_used_the_last_change_starts_no_second_process(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", OOM_ALWAYS, where="runtime")
    result = scheduler_for(machine=_with(SMALL)).run(_one_node("test.fit"), events.append)
    assert result.error is not None and result.error["kind"] == "oom"
    assert result.error["message"].startswith("Ran out of memory, and nothing smaller")
    assert events.kinds().count("node.start") == 1 and events.kinds().count("node.fit") == 2


# -- from the review -----------------------------------------------------------------------------


class _Recorder(LearnedStore):
    def __init__(self, fail: bool = False) -> None:
        super().__init__(None)
        self.fail = fail
        self.calls: list[dict[str, Any]] = []

    def record(self, key: StoreKey, kind: str, **measured: Any) -> None:
        self.calls.append({"kind": kind, **measured})
        if self.fail:
            raise PermissionError("learned.json is locked")


def test_a_processor_run_learns_with_nothing_outside_torch(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    model = fit_node.model()
    model["outside_torch_mb"] = {"mb": 800, "source": fit_node.SOURCE}
    make_node(
        {
            "id": "test.outside",
            "version": "1",
            "title": "Outside",
            "category": "evaluate",
            "outputs": {"text": "Text"},
            "params": fit_node.PARAMS,
            "devices": ["cuda", "cpu"],
            "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
            "memory": model,
        },
        SEEN.format(extra="    pass"),
    )
    store = _Recorder()
    result = scheduler_for(machine=_with(ROOMY), learned=store).run(
        _one_node("test.outside"), lambda _e: None
    )
    assert result.status == "done", result.error
    assert [(c["kind"], c["outside_mb"]) for c in store.calls] == [("cpu", 0.0)]


def test_a_failure_to_record_what_was_learned_never_fails_the_run(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.fit", where="runtime")
    result = scheduler_for(machine=_with(ROOMY), learned=_Recorder(fail=True)).run(
        _one_node("test.fit"), events.append
    )
    assert result.status == "done", result.error
    assert events.kinds()[-2:] == ["node.done", "run.done"]


def test_a_card_with_no_room_left_is_capped_at_its_free_memory_not_at_a_budget_below_zero(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _fit_node(make_node, "test.plain", where="runtime", model=False)
    full = machines.machine(
        [machines.card(0, "8.6", 12885, 1200)], 10000, 17180
    )  # 1200 free, under the margin
    result = scheduler_for(machine=_with(full), runtimes=HostRuntimes(target=Target(card=0, lock="l"))).run(
        _one_node("test.plain"), events.append
    )
    assert result.status == "done", result.error
    (fitted,) = events.of("node.fit")
    assert fitted["device"] == "cuda" and fitted["devices"]["cuda"]["budget"] < 0
    (ceiling,) = events.of("ceiling")
    assert ceiling["budget_mb"] is None  # the child caps at the free memory it measures instead


def test_an_engine_bug_is_not_blamed_on_the_node(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only a node that breaks its manifest fails with kind contract. A ValueError from the engine's
    own code goes up as the engine's bug (the server reports run.failed with kind engine)."""
    make_node(
        {
            "id": "test.fine",
            "version": "1",
            "title": "fine",
            "category": "evaluate",
            "outputs": {"text": "Text"},
        },
        "def run(ctx):\n    p = ctx.path('t.json')\n    p.write_text('1')\n    ctx.output('text', p)\n",
    )

    def broken(*_args: object, **_kwargs: object) -> str:
        raise ValueError("a bug in the engine")

    monkeypatch.setattr(Cache, "key", broken)
    with pytest.raises(ValueError, match="a bug in the engine"):
        scheduler_for().run(_graph({"f": {"node": "test.fine"}}, []), events.append)
    assert not events.of("node.failed")


def test_a_node_gives_and_takes_a_collection(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """A node declares three items; a node whose input is a collection receives them in order,
    each with its own facets and a key of its own (spec 021, AC1)."""
    make_node(MASKS["manifest"], MASKS["code"])
    make_node(TAKE_MASKS["manifest"], TAKE_MASKS["code"])
    graph = _graph({"m": {"node": "test.masks"}, "t": {"node": "test.take_masks"}}, [("m.masks", "t.masks")])
    result = scheduler_for().run(graph, events.append)
    assert result.status == "done", result.error
    masks = result.collections["m"]["masks"]
    entry = next(e["key"] for e in events.of("node.done") if e["step"] == "m")
    assert [v.key for v in masks] == [f"{entry}:masks:{i}" for i in range(3)]
    assert [v.facets for v in masks] == [{"kind": "binary", "marks": m} for m in ("object", "valid", "sky")]
    assert [v.trust for v in masks] == ["predicted"] * 3
    seen = json.loads(result.outputs["t"]["seen"].path.read_text(encoding="utf-8"))
    assert [(row["key"], row["facets"], row["bytes"]) for row in seen] == [
        (v.key, v.facets, i) for i, v in enumerate(masks)
    ]
    done = next(e for e in events.of("node.done") if e["step"] == "m")
    assert done["outputs"]["masks"]["type"] == "Mask"
    assert [item["key"] for item in done["outputs"]["masks"]["items"]] == [v.key for v in masks]
    assert events.of("run.done")[0]["outputs"]["m"]["masks"]["items"][2]["facets"]["marks"] == "sky"
    # Taken whole, a collection gives the fit one factor, `<input>.count` (spec 021).
    [take] = [step for step in plan(graph, scheduler_for().registry).steps if step.id == "t"]
    assert _meta(take, {"masks": masks}) == {"masks": {"count": 3}}
    events.clear()  # from the cache, the collection is the same, in the same order
    again = scheduler_for().run(graph, events.append)
    assert events.kinds() == ["run.start", "node.cached", "node.cached", "run.done"]
    assert [v.key for v in again.collections["m"]["masks"]] == [v.key for v in masks]


def _contract_failure(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], code: str, **ports: str
) -> str:
    """The message a node with these outputs fails with, by its code."""
    make_node(
        {"id": "test.bad", "version": "1", "title": "Bad", "category": "evaluate", "outputs": ports},
        code,
    )
    events = Events()
    result = scheduler_for().run(_graph({"b": {"node": "test.bad"}}, []), events.append)
    [failed] = events.of("node.failed")
    assert (result.status, failed["kind"]) == ("failed", "contract"), failed
    return str(failed["message"])


ITEM = """
def run(ctx):
    for i, facets in enumerate(FACETS):
        p = ctx.path(f"m{i}.png")
        p.write_bytes(b"x")
        ctx.output_item("masks", p, facets=facets)
"""


def test_an_item_is_checked_as_a_single_value_is(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    """Each item is held to the manifest and the type, as a value is, and named by its index; a
    collection port is given item by item, a single one not (spec 021)."""
    unsaid = "FACETS = [{'kind': 'binary', 'marks': 'sky'}, {'kind': 'binary'}]\n" + ITEM
    message = _contract_failure(make_node, scheduler_for, unsaid, masks="Mask*")
    assert "test.bad.masks[1] did not say its marks" in message, message
    contradicts = "FACETS = [{'kind': 'soft', 'marks': 'sky'}]\n" + ITEM
    message = _contract_failure(make_node, scheduler_for, contradicts, masks="Mask[kind=binary]*")
    assert "masks[0]: kind=soft contradicts its manifest" in message, message
    whole = "def run(ctx):\n    p = ctx.path('m.png')\n    p.write_bytes(b'x')\n    ctx.output('masks', p)\n"
    message = _contract_failure(make_node, scheduler_for, whole, masks="Mask[kind=binary, marks=sky]*")
    assert "given with ctx.output" in message, message
    single = "FACETS = [{}]\n" + ITEM
    message = _contract_failure(make_node, scheduler_for, single, masks="Mask[kind=binary, marks=sky]")
    assert "is a single Mask, given with ctx.output_item" in message, message


def test_collections_of_one_step_have_one_length(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """The collections one node gives line up item for item; a port with no item is empty."""
    code = (
        "def run(ctx):\n"
        "    for port, n in (('masks', 3), ('boxes', 2)):\n"
        "        for i in range(n):\n"
        "            p = ctx.path(f'{port}{i}.png')\n"
        "            p.write_bytes(b'x')\n"
        "            ctx.output_item(port, p)\n"
    )
    spec = "Mask[kind=binary, marks=object]*"
    message = _contract_failure(make_node, scheduler_for, code, masks=spec, boxes=spec)
    assert "collections of different lengths: masks 3, boxes 2" in message, message
    make_node(
        {
            "id": "test.none",
            "version": "1",
            "title": "None",
            "category": "evaluate",
            "outputs": {"masks": spec},
        }
    )
    result = scheduler_for().run(_graph({"n": {"node": "test.none"}}, []), events.append)
    assert result.status == "done", result.error
    assert result.collections["n"]["masks"] == []


def _for_each(n: int = 3, fail_at: int = -1) -> Graph:
    """`n` masks, each run through test.per_mask with one image, and the results taken whole."""
    return _graph(
        {
            "i": {"node": "test.image"},
            "m": {"node": "test.masks", "params": {"n": n}},
            "p": {"node": "test.per_mask", "params": {"fail_at": fail_at}},
            "t": {"node": "test.take_masks"},
        },
        [("m.masks", "p.mask"), ("i.image", "p.image"), ("p.mask", "t.masks")],
    )


@pytest.fixture
def each_nodes(make_node: MakeNode, basic_nodes: None) -> None:
    for node in (MASKS, PER_MASK, TAKE_MASKS):
        make_node(node["manifest"], node["code"])


def _of(events: Events, kind: str, step: str = "p") -> list[tuple[int | None, int | None]]:
    """The `item` and `of` of each `kind` event of `step`, in order."""
    return [(e.get("item"), e.get("of")) for e in events.of(kind) if e["step"] == step]


@pytest.mark.usefixtures("each_nodes")
def test_a_single_input_fed_a_collection_runs_for_each(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Three items and one single value: three runs, each with one item and the value, each run's
    events carrying `item` and `of`; a node that takes the whole receives the three results in the
    items' order (spec 021, AC2)."""
    result = scheduler_for().run(_for_each(), events.append)
    assert result.status == "done", result.error
    assert _of(events, "node.start") == [(0, 3), (1, 3), (2, 3)]
    assert _of(events, "node.done") == [(0, 3), (1, 3), (2, 3)]
    image = result.outputs["i"]["image"].path.read_bytes()
    made = result.collections["p"]["mask"]
    assert [v.path.read_bytes() for v in made] == [bytes([i]) * 16 + image for i in range(3)]
    assert [v.facets["marks"] for v in made] == ["object", "valid", "sky"]
    keys = [e["key"] for e in events.of("node.done") if e["step"] == "p"]
    assert [v.key for v in made] == [f"{key}:mask" for key in keys]  # each its own run's entry
    seen = json.loads(result.outputs["t"]["seen"].path.read_text(encoding="utf-8"))
    assert [row["key"] for row in seen] == [v.key for v in made]
    assert events.of("run.done")[0]["outputs"]["p"]["mask"]["items"][1]["key"] == made[1].key
    assert all(e.get("item") is None for e in events if e.get("step") in ("i", "m", "t"))


@pytest.mark.usefixtures("each_nodes")
def test_for_each_reruns_only_the_items_that_changed(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """After a run over three items, a run over those three and a fourth starts the step once, for
    the fourth, and answers the other three from the cache (spec 021, AC3)."""
    sched = scheduler_for()
    first = sched.run(_for_each(3), events.append)
    assert first.status == "done", first.error
    events.clear()
    second = sched.run(_for_each(4), events.append)
    assert second.status == "done", second.error
    assert _of(events, "node.start", "m") == [(None, None)]  # the source ran again, for its fourth
    assert _of(events, "node.start") == [(3, 4)]
    assert _of(events, "node.cached") == [(0, 4), (1, 4), (2, 4)]
    assert [v.key for v in second.collections["p"]["mask"][:3]] == [
        v.key for v in first.collections["p"]["mask"]
    ]


@pytest.mark.usefixtures("each_nodes")
def test_a_failed_item_fails_its_step(scheduler_for: Callable[..., Scheduler], events: Events) -> None:
    """The second of three items fails: the step ends node.failed naming item 1, the third never
    starts, a dependent step is skipped, and a second run answers the first item from the cache
    (spec 021, AC5)."""
    sched = scheduler_for()
    result = sched.run(_for_each(3, fail_at=1), events.append)
    assert (result.status, result.failed, result.skipped) == ("failed", ["p"], ["t"])
    [failed] = events.of("node.failed")
    assert (failed["step"], failed["item"], failed["of"], failed["kind"]) == ("p", 1, 3, "node")
    assert failed["message"] == "mask 1 fails"
    assert _of(events, "node.start") == [(0, 3), (1, 3)]
    assert [e["waited_on"] for e in events.of("node.skipped")] == ["p"]
    assert events.of("run.failed")[0]["step"] == "p"
    events.clear()
    sched.run(_for_each(3, fail_at=1), events.append)
    assert _of(events, "node.cached") == [(0, 3)]
    assert _of(events, "node.start") == [(1, 3)]


@pytest.mark.usefixtures("each_nodes")
def test_an_empty_collection_runs_nothing_and_says_so(
    scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """A for each over no items starts nothing, says node.done with `of` 0, hands a node that takes
    the whole an empty list, and the run ends done (spec 021, AC6)."""
    result = scheduler_for().run(_for_each(0), events.append)
    assert result.status == "done", result.error
    assert _of(events, "node.start") == []
    [done] = [e for e in events.of("node.done") if e["step"] == "p"]
    assert (done["of"], done["outputs"]) == (0, {"mask": {"type": "Mask", "items": []}})
    assert "item" not in done and "key" not in done
    assert result.collections["p"]["mask"] == []
    assert json.loads(result.outputs["t"]["seen"].path.read_text(encoding="utf-8")) == []
    assert events.kinds()[-1] == "run.done"


# -- spec 023: several runs at once, a slot per device -------------------------------------------


def _runs_at_once(
    scheduler: Scheduler, events: Events, stop: threading.Event
) -> Callable[[str, Graph], threading.Thread]:
    """Start runs of `scheduler` beside each other, each on its own thread, by the id given."""

    def start(run_id: str, graph: Graph) -> threading.Thread:
        args = (graph, events.append, stop, run_id)
        thread = threading.Thread(target=scheduler.run, args=args, daemon=True)
        thread.start()
        return thread

    return start


def _said(events: Events, run: str, kind: str) -> list[dict[str, Any]]:
    return [e for e in list(events) if e.get("run") == run and e["event"] == kind]


def _at(events: Events, run: str, kind: str) -> int:
    """Where a run's first event of a kind was said, among all the events."""
    return next(i for i, e in enumerate(list(events)) if e.get("run") == run and e["event"] == kind)


def test_a_step_waits_for_its_device_in_turn(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    gates: Gates,
    stop_after: Callable[[float], threading.Event],
) -> None:
    """Spec 023, AC2: of three runs with a step on the processor, the second and third say
    node.queued, naming the run and step ahead, and start in the order they asked, each after the
    one before it has ended."""
    make_node(hold_manifest("test.hold", where="runtime"), HOLD_CODE)
    start = _runs_at_once(scheduler_for(machine=_with(ROOMY)), events, stop_after(60))
    threads = [start("a", _one_node("test.hold", gate=gates("a")))]
    wait_until(lambda: _said(events, "a", "stage"), lambda: f"a's step to hold: {events.kinds()}", 30)
    threads.append(start("b", _one_node("test.hold", gate=gates("b"))))
    wait_until(lambda: _said(events, "b", "node.queued"), "b's step to queue")
    threads.append(start("c", _one_node("test.hold", gate=gates("c"))))
    wait_until(lambda: _said(events, "c", "node.queued"), "c's step to queue")
    ((b,), (c,)) = _said(events, "b", "node.queued"), _said(events, "c", "node.queued")
    assert (b["step"], b["node"], b["device"]) == ("n", "test.hold", "cpu")
    assert b["behind"] == {"run": "a", "step": "n"}, "b waits behind the step that holds the processor"
    assert c["behind"] == {"run": "b", "step": "n"}, "c waits behind the one that asked before it"
    assert not _said(events, "b", "node.fit") and not _said(events, "c", "node.fit"), "neither is fitted yet"

    for name, following in (("a", "b"), ("b", "c"), ("c", None)):
        gates.open(name)
        wait_until(lambda run=name: _said(events, run, "run.done"), f"run {name} to end")
        if following is not None:
            held = lambda run=following: _said(events, run, "stage")  # noqa: E731
            wait_until(held, f"run {following} to hold the processor")
            assert _at(events, name, "node.done") < _at(events, following, "node.start")
    for thread in threads:
        thread.join(30)
    assert [e["run"] for e in events.of("node.start")] == ["a", "b", "c"]


def test_a_step_is_fitted_only_once_it_holds_its_slot(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    gates: Gates,
    stop_after: Callable[[float], threading.Event],
) -> None:
    """Spec 023, AC3: while a first run's step holds the card, the machine reports the card full;
    a second run's step for the card says node.queued and is not fitted until the first has ended,
    then once, on the card at its own values, and ends done."""
    make_node(
        hold_manifest("test.hold_card", "runtime", ("cuda", "cpu"), fit_node.PARAMS, fit_node.model()),
        HOLD_CODE,
    )
    held = threading.Event()  # the first step holds the card: from its node.start to its node.done

    def listen(event: dict[str, Any]) -> None:
        if event.get("run") == "first" and event["event"] == "node.start":
            held.set()
        if event.get("run") == "first" and event["event"] == "node.done":
            held.clear()
        events.append(event)

    def machine(_cards: bool) -> dict[str, object]:
        free = 500 if held.is_set() else 24500
        return machines.machine([machines.card(0, "8.9", 25770, free)], 50000, 68719)

    scheduler = scheduler_for(machine=machine, runtimes=HostRuntimes(target=Target(card=0, lock="l")))
    stop = stop_after(60)
    first = threading.Thread(
        target=scheduler.run, args=(_one_node("test.hold_card", gate=gates("first")), listen, stop, "first")
    )
    first.start()
    wait_until(
        lambda: _said(events, "first", "stage"), lambda: f"the first step to hold: {events.kinds()}", 30
    )
    second = threading.Thread(
        target=scheduler.run, args=(_one_node("test.hold_card", gate=gates("second")), listen, stop, "second")
    )
    second.start()
    wait_until(lambda: _said(events, "second", "node.queued"), "the second step to queue for the card")
    (queued,) = _said(events, "second", "node.queued")
    assert (queued["device"], queued["card"], queued["behind"]) == ("cuda", 0, {"run": "first", "step": "n"})
    assert not _said(events, "second", "node.fit"), "not fitted while the first holds the card"

    gates.open("first")
    wait_until(lambda: _said(events, "first", "run.done"), "the first run to end")
    gates.open("second")
    wait_until(
        lambda: _said(events, "second", "run.done") or _said(events, "second", "run.failed"), "the second run"
    )
    first.join(30)
    second.join(30)
    assert _said(events, "second", "run.done"), _said(events, "second", "run.failed")
    (fitted,) = _said(events, "second", "node.fit")
    assert _at(events, "first", "node.done") < _at(events, "second", "node.fit")
    assert (fitted["device"], fitted["outcome"], fitted["changes"]) == ("cuda", "fits", [])
    (alone,) = _said(events, "first", "node.fit")  # the same step on a free card: its own values
    assert {**fitted["values"], "gate": None} == {**alone["values"], "gate": None}
    assert fitted["values"]["resolution"] == 1024


def test_the_same_work_in_two_runs_is_done_once(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    gates: Gates,
    stop_after: Callable[[float], threading.Event],
) -> None:
    """Spec 023, AC5: two runs of one graph started together call the node's code once; the second
    run's step waits for the first's, saying so, and is node.cached."""
    make_node(hold_manifest("test.hold"), HOLD_CODE)
    start = _runs_at_once(scheduler_for(), events, stop_after(60))
    graph = _one_node("test.hold", gate=gates("once"))
    threads = [start("a", graph)]
    wait_until(lambda: _said(events, "a", "stage"), lambda: f"a's step to hold: {events.kinds()}")
    threads.append(start("b", graph))
    wait_until(lambda: _said(events, "b", "node.queued"), "b's step to wait for a's")
    (queued,) = _said(events, "b", "node.queued")
    assert queued["behind"] == {"run": "a", "step": "n"} and "device" not in queued
    gates.open("once")
    for thread in threads:
        thread.join(30)
    assert _said(events, "a", "run.done") and _said(events, "b", "run.done"), events.kinds()
    assert gates.calls("once") == 1, "the node's code ran once"
    (cached,) = _said(events, "b", "node.cached")
    assert cached["key"] == _said(events, "a", "node.done")[0]["key"]
    assert not _said(events, "b", "node.start")


def test_a_place_whose_wait_ends_leaves_the_line() -> None:
    """A place that stops while it waits, or whose node.queued cannot be said, leaves the slot's
    line, so the slot goes to the next place and never to one that is gone (spec 023)."""
    slots, slot = Slots(), Slot("cpu")
    first, gone, broken, last = (Place(run, "n", {}) for run in ("first", "gone", "broken", "last"))
    never = threading.Event()
    assert slots.take(slot, first, never, lambda _ahead: None) is False

    stopped = threading.Event()
    stopped.set()
    with pytest.raises(Stopped):
        slots.take(slot, gone, stopped, lambda _ahead: None)

    def cannot_say(_ahead: Place) -> None:
        raise OSError("the app's pipe is gone")

    with pytest.raises(OSError):
        slots.take(slot, broken, never, cannot_say)
    behind: list[Place] = []
    taken = threading.Thread(target=slots.take, args=(slot, last, never, behind.append))
    taken.start()
    wait_until(lambda: behind, "the last place to say what it waits behind")
    assert behind == [first], "neither the stopped place nor the broken one is in the line"
    slots.give(slot, first)
    taken.join(10)
    assert not taken.is_alive(), "the slot went to the last place"
    slots.give(slot, last)


def test_a_step_that_tries_the_processor_first_goes_beside_a_card_run(
    make_node: MakeNode,
    scheduler_for: Callable[..., Scheduler],
    events: Events,
    gates: Gates,
    stop_after: Callable[[float], threading.Event],
) -> None:
    """A step whose node lists the processor before the card takes the processor's slot, as its fit
    would choose it, so it does not wait for a run that holds the card (spec 023)."""
    make_node(hold_manifest("test.card_first", "runtime", ("cuda", "cpu")), HOLD_CODE)
    make_node(hold_manifest("test.cpu_first", "runtime", ("cpu", "cuda")), HOLD_CODE)
    card = machines.machine([machines.CARD_24GB], 50000, 68719)
    scheduler = scheduler_for(machine=_with(card), runtimes=HostRuntimes(target=Target(card=0, lock="l")))
    start = _runs_at_once(scheduler, events, stop_after(60))
    threads = [start("card", _one_node("test.card_first", gate=gates("card")))]
    wait_until(lambda: _said(events, "card", "stage"), lambda: f"the card run to hold: {events.kinds()}", 30)
    threads.append(start("cpu", _one_node("test.cpu_first", gate=gates("cpu"))))
    wait_until(
        lambda: _said(events, "cpu", "stage"), lambda: f"the processor run to hold: {events.kinds()}", 30
    )
    assert not events.of("node.queued")
    assert _said(events, "cpu", "node.fit")[0]["device"] == "cpu"
    gates.open("card")
    gates.open("cpu")
    for thread in threads:
        thread.join(30)
