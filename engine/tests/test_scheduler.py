from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import pytest
from conftest import Events, MakeNode
from fixtures import fit_node, machines

from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.memory import Learned, LearnedStore, Settings, StoreKey, Target
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
            "import pathlib, tempfile\ndef run(ctx):\n"
            "    p = pathlib.Path(tempfile.mkdtemp()) / 'd.npz'; p.write_bytes(b'x')\n"
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
        "category": "test",
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
            "category": "test",
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
            "category": "test",
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
    on_card = scheduler_for(machine=_with(card), target=lambda _r: Target(card=0, lock="l")).run(
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
    calls = {"n": 0}

    def stop() -> bool:
        calls["n"] += 1
        return calls["n"] > 1  # not before the step; at the node's own check

    result = scheduler_for(machine=_with(TIGHT)).run(_one_node("test.fit"), events.append, should_stop=stop)
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
    _fit_node(make_node, "test.fit", OOM_FIRST, where="runtime")
    pids: list[int] = []
    result = scheduler_for(
        machine=_with(machines.get("8 GB, compute 6.1, 1.5 GB held")),
        target=lambda _r: Target(card=0, lock="l"),
        on_pid=pids.append,
    ).run(_one_node("test.fit"), events.append)
    assert result.status == "done", result.error
    assert len(set(pids)) == 2
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
            machine=_with(ROOMY, cards=cards), target=lambda _r, c=card: Target(card=c, lock="l")
        ).run(_one_node(node, resolution=resolution), lambda _e: None)
        assert cards == expected, node


def test_a_runtime_node_teaches_this_machine_and_an_engine_node_does_not(
    tmp_path: Path, make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    _fit_node(make_node, "test.engine")
    _fit_node(make_node, "test.runtime", where="runtime")
    store = LearnedStore(tmp_path / "data")
    for node in ("test.engine", "test.runtime"):
        scheduler_for(machine=_with(ROOMY), learned=store, target=lambda _r: Target(lock="l")).run(
            _one_node(node), lambda _e: None
        )
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
            "category": "test",
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
    result = scheduler_for(machine=_with(full), target=lambda _r: Target(card=0, lock="l")).run(
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
        {"id": "test.fine", "version": "1", "title": "fine", "category": "test", "outputs": {"text": "Text"}},
        "def run(ctx):\n    p = ctx.path('t.json')\n    p.write_text('1')\n    ctx.output('text', p)\n",
    )

    def broken(*_args: object, **_kwargs: object) -> str:
        raise ValueError("a bug in the engine")

    monkeypatch.setattr(Cache, "key", broken)
    with pytest.raises(ValueError, match="a bug in the engine"):
        scheduler_for().run(_graph({"f": {"node": "test.fine"}}, []), events.append)
    assert not events.of("node.failed")
