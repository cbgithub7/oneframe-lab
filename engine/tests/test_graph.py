from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeNode

from oneframe.graph import Graph, GraphError, fits, plan
from oneframe.inputs import find, import_file
from oneframe.layout import Layout
from oneframe.registry import Registry
from oneframe.scheduler import Scheduler


def _graph(nodes: Mapping[str, Mapping[str, object]], edges: list[tuple[str, str]]) -> Graph:
    return Graph.from_json(
        {"version": 1, "nodes": dict(nodes), "edges": [{"from": a, "to": b} for a, b in edges]}
    )


def _messages(exc: GraphError) -> str:
    return " | ".join(f"{p['node']}.{p['port']}: {p['message']}" for p in exc.problems)


@pytest.mark.usefixtures("basic_nodes")
def test_plan_orders_nodes_and_fills_defaults(registry_for: Callable[[], Registry]) -> None:
    g = _graph({"d": {"node": "test.const_depth"}, "i": {"node": "test.image"}}, [("i.image", "d.image")])
    p = plan(g, registry_for())
    assert p.order == ["i", "d"]
    assert p.steps[1].params == {"z": 2.0, "batch": 1}
    assert p.steps[1].inputs == {"image": ("i", "image")}


@pytest.mark.usefixtures("basic_nodes")
def test_plan_reports_every_problem_with_its_node_and_port(registry_for: Callable[[], Registry]) -> None:
    g = _graph(
        {
            "d": {"node": "test.const_depth", "params": {"z": -1, "colour": "red"}},
            "x": {"node": "test.nothing"},
            "i": {"node": "test.image"},
        },
        [("i.image", "d.nope"), ("ghost.image", "d.image")],
    )
    with pytest.raises(GraphError) as info:
        plan(g, registry_for())
    text = _messages(info.value)
    for needle in (
        "below its minimum",
        "no parameter colour",
        "no node called 'test.nothing'",
        "no input 'nope'",
        "not in the graph",
        "input image (Image) is not connected",
    ):
        assert needle in text


@pytest.mark.usefixtures("basic_nodes")
def test_plan_refuses_type_mismatch_and_cycles(
    make_node: MakeNode, registry_for: Callable[[], Registry]
) -> None:
    make_node(
        {
            "id": "test.loop",
            "version": "1",
            "title": "L",
            "category": "evaluate",
            "inputs": {"depth": "Depth"},
            "outputs": {"depth": "Depth[kind=metric, measure=z]"},
        }
    )
    g = _graph(
        {"a": {"node": "test.loop"}, "b": {"node": "test.loop"}},
        [("a.depth", "b.depth"), ("b.depth", "a.depth")],
    )
    with pytest.raises(GraphError, match="cycle"):
        plan(g, registry_for())
    g = _graph({"i": {"node": "test.image"}, "a": {"node": "test.loop"}}, [("i.image", "a.depth")])
    with pytest.raises(GraphError, match="Image cannot connect to Depth"):
        plan(g, registry_for())


def test_a_converter_is_inserted_between_facets_that_differ(
    make_node: MakeNode, registry_for: Callable[[], Registry]
) -> None:
    make_node(
        {
            "id": "test.relative",
            "version": "1",
            "title": "R",
            "category": "evaluate",
            "outputs": {"depth": "Depth[kind=affine_invariant_disparity, measure=z]"},
        }
    )
    make_node(
        {
            "id": "test.needs_metric",
            "version": "1",
            "title": "M",
            "category": "evaluate",
            "inputs": {"depth": "Depth[kind=metric]"},
            "outputs": {"points": "PointMap"},
        }
    )
    g = _graph({"r": {"node": "test.relative"}, "m": {"node": "test.needs_metric"}}, [("r.depth", "m.depth")])
    with pytest.raises(GraphError, match="needs metric"):
        plan(g, registry_for())
    make_node(
        {
            "id": "test.to_metric",
            "version": "1",
            "title": "To metric",
            "category": "convert",
            "inputs": {"depth": "Depth[kind=affine_invariant_disparity]"},
            "outputs": {"depth": "Depth[kind=metric, measure=z]"},
            "params": {"scale": {"type": "float", "default": 1.0}},
        }
    )
    p = plan(g, registry_for())
    assert p.order == ["r", "m~depth~test.to_metric", "m"]
    assert p.steps[2].inputs["depth"] == ("m~depth~test.to_metric", "depth")
    assert "Inserted To metric" in p.notes[0]


def test_recipe_round_trips_and_rejects_other_versions() -> None:
    data = {
        "version": 1,
        "title": "T",
        "nodes": {"a": {"node": "x", "params": {"k": 1}, "label": "A"}},
        "edges": [{"from": "a.o", "to": "b.i"}],
    }
    assert Graph.from_json(data).to_json() == data
    with pytest.raises(GraphError, match="version 2"):
        Graph.from_json(dict(data, version=2))
    with pytest.raises(GraphError, match="version True"):
        Graph.from_json(dict(data, version=True))


def test_page_fields_survive_a_round_trip() -> None:
    """The page's own fields, on the graph and on a node, come back exactly; the engine reads none."""
    data = {
        "version": 1,
        "title": "Two results",
        "nodes": {
            "a": {
                "node": "x",
                "params": {"k": 1},
                "page": {"at": [120.5, -40], "shown": ["o"], "open": True},
            },
            "b": {
                "node": "y",
                "params": {},
                "label": "B",
                "page": {"card": {"folded": False, "notes": None}},
            },
            "c": {"node": "z", "params": {}},
        },
        "edges": [{"from": "a.o", "to": "b.i"}],
        "page": {"panes": [{"step": "b", "port": "p", "zoom": 1.25}], "layout": {"split": [0.5, 0.5]}},
    }
    written = json.loads(json.dumps(Graph.from_json(data).to_json()))
    assert written == data
    assert Graph.from_json(written).to_json() == data


def test_a_graph_of_the_wrong_shape_names_each_problem() -> None:
    with pytest.raises(GraphError) as info:
        Graph.from_json(
            {
                "version": 1,
                "title": 3,
                "nodes": {"a": {"params": []}, "b": "x", "c": {"node": "y", "page": "left"}},
                "edges": [{"from": "a"}, "a.o"],
                "page": [],
            }
        )
    text = _messages(info.value)
    for needle in (
        "title should be text",
        "a.: a node should name",
        "a.: params should be an object",
        "b.: a node should be an object",
        "c.: page should be an object",
        "an edge should read",
        ".: page should be an object",
    ):
        assert needle in text
    assert len(info.value.problems) == 8
    with pytest.raises(GraphError, match="should be a JSON object"):
        Graph.from_json([])


# A registry where every kind of answer occurs: a converter, an output that leaves a facet open, an
# input that is fed, and an edge that would close a cycle. Every input is optional, so a graph
# with one more edge, or one more node, plans unless that edge is refused.
FITS_NODES = [
    {
        "id": "test.src",
        "outputs": {
            "metric": "Depth[kind=metric, measure=z]",
            "open": "Depth[measure=z]",
            "rel": "Depth[kind=affine_invariant_disparity, measure=z]",
            "image": "Image[alpha=none]",
        },
    },
    {
        "id": "test.sink",
        "inputs": {
            "metric": {"type": "Depth[kind=metric]", "optional": True},
            "any": {"type": "Depth", "optional": True},
            "image": {"type": "Image", "optional": True},
        },
        "outputs": {"depth": "Depth[kind=metric, measure=z]"},
    },
    {
        "id": "test.to_metric",
        "category": "convert",
        "inputs": {"depth": {"type": "Depth[kind=affine_invariant_disparity]", "optional": True}},
        "outputs": {"depth": "Depth[kind=metric, measure=z]"},
        "params": {"scale": {"type": "float", "default": 1.0}},
    },
]
FITS_GRAPH = {
    "version": 1,
    "nodes": {
        k: {"node": n}
        for k, n in [("s", "test.src"), ("k", "test.sink"), ("k2", "test.sink"), ("c", "test.to_metric")]
    },
    "edges": [
        {"from": "s.metric", "to": "k.metric"},
        {"from": "k.depth", "to": "k2.any"},
        {"from": "s.rel", "to": "c.depth"},
    ],
}


def _planned(data: dict[str, Any], src: str, dst: str, registry: Registry) -> dict[str, Any]:
    """What plan() makes of the graph with the edge src -> dst added: how fits() would say it."""
    graph = Graph.from_json(dict(data, edges=[*data["edges"], {"from": src, "to": dst}]))
    step, _, port = dst.partition(".")
    try:
        made = plan(graph, registry)
    except GraphError as exc:
        mine = [
            p["message"]
            for p in exc.problems
            if (p["node"], p["port"]) == (step, port) or "cycle" in p["message"]
        ]
        assert mine, f"{src} -> {dst}: a problem that is not the edge's: {_messages(exc)}"
        return {
            "how": "no",
            "cycle": any("cycle" in m for m in mine),
            "taken": any("two edges feed" in m for m in mine),
        }
    inserted = [s.manifest.id for s in made.steps if s.id.startswith(f"{step}~{port}~")]
    each = port in next(s for s in made.steps if s.id == step).each  # the step runs for each
    if inserted:
        return {"how": "via", "converter": inserted[0], "each": each}
    return {"how": "depends" if {"from": src, "to": dst} in made.depends else "direct", "each": each}


def _agrees(answer: dict[str, Any], planned: dict[str, Any], edge: str) -> None:
    assert answer["how"] == planned["how"], f"{edge}: fits says {answer}, plan {planned}"
    if answer["how"] != "no":
        assert answer.get("each", False) == planned["each"], f"{edge}: fits says {answer}, plan {planned}"
    if answer["how"] == "via":
        assert answer["converter"] == planned["converter"], edge
    if answer["how"] == "no":
        assert (answer["reason"] == "taken") == planned["taken"], f"{edge}: {answer}"
        if planned["cycle"]:
            assert answer["reason"] == "cycle", f"{edge}: {answer}"


def _every_pair(graph_data: dict[str, Any], registry: Registry) -> set[str]:
    """fits() against plan() for every edge from or to a port of the graph, in it and to a node
    added; each kind of answer seen, as `how:reason`, with `+each` where the step runs for each."""
    graph = Graph.from_json(graph_data)
    seen: set[str] = set()

    def side(answers: list[dict[str, Any]], which: str) -> list[dict[str, Any]]:
        # A port named on both sides of its node (the converter's depth) is answered for both.
        return [a for a in answers if a["side"] == which]

    for key, gnode in graph.nodes.items():
        manifest = registry.get(gnode.node)
        for out in manifest.outputs:
            answers = fits(graph, f"{key}.{out}", registry)
            for answer in side(answers["graph"], "input"):
                edge = (f"{key}.{out}", f"{answer['step']}.{answer['port']}")
                _agrees(answer, _planned(graph_data, *edge, registry), " -> ".join(edge))
                back = side(fits(graph, edge[1], registry)["graph"], "output")
                [mirror] = [a for a in back if (a["step"], a["port"]) == (key, out)]
                assert {k: mirror[k] for k in ("how", "converter", "reason", "each") if k in mirror} == {
                    k: answer[k] for k in ("how", "converter", "reason", "each") if k in answer
                }, edge
                seen.add(
                    answer["how"] + ":" + answer.get("reason", "") + ("+each" if answer.get("each") else "")
                )
            for answer in side(answers["add"], "input"):  # a new node of each kind, fed from here
                data = dict(graph_data, nodes={**graph_data["nodes"], "new": {"node": answer["node"]}})
                edge = (f"{key}.{out}", f"new.{answer['port']}")
                _agrees(answer, _planned(data, *edge, registry), " -> ".join(edge))
                seen.add(
                    answer["how"] + ":" + answer.get("reason", "") + ("+each" if answer.get("each") else "")
                )
        for into in manifest.inputs:
            for answer in side(fits(graph, f"{key}.{into}", registry)["add"], "output"):  # feeding it
                data = dict(graph_data, nodes={**graph_data["nodes"], "new": {"node": answer["node"]}})
                edge = (f"new.{answer['port']}", f"{key}.{into}")
                _agrees(answer, _planned(data, *edge, registry), " -> ".join(edge))
    return seen


def test_what_fits_agrees_with_plan(make_node: MakeNode, registry_for: Callable[[], Registry]) -> None:
    for row in FITS_NODES:
        make_node({"version": "1", "title": row["id"], "category": "evaluate", **row})
    registry = registry_for()
    graph = Graph.from_json(FITS_GRAPH)
    seen = _every_pair(FITS_GRAPH, registry)
    assert seen >= {"direct:", "via:", "depends:", "no:mismatch", "no:taken", "no:cycle"}, seen
    with pytest.raises(GraphError, match="no port"):
        fits(graph, "s.nope", registry)


# Collections (spec 021): a node that gives them and a single value, one that takes either, item
# by item or whole, and the converter. Every input is optional, as above.
EACH_NODES = [
    {
        "id": "test.many",
        "outputs": {
            "rels": "Depth[kind=affine_invariant_disparity, measure=z]*",
            "metrics": "Depth[kind=metric, measure=z]*",
            "one": "Depth[kind=metric, measure=z]",
        },
    },
    {
        "id": "test.each_sink",
        "inputs": {
            "metric": {"type": "Depth[kind=metric]", "optional": True},
            "all": {"type": "Depth[kind=metric]*", "optional": True},
        },
        "outputs": {"depth": "Depth[kind=metric, measure=z]"},
    },
    FITS_NODES[2],
]
EACH_GRAPH = {
    "version": 1,
    "nodes": {
        k: {"node": n}
        for k, n in [
            ("m", "test.many"),
            ("k", "test.each_sink"),
            ("k2", "test.each_sink"),
            ("c", "test.to_metric"),
        ]
    },
    "edges": [{"from": "m.metrics", "to": "k.metric"}],  # k runs for each: its depth is a collection
}


def test_what_fits_says_each(make_node: MakeNode, registry_for: Callable[[], Registry]) -> None:
    for row in EACH_NODES:
        make_node({"version": "1", "title": row["id"], "category": "evaluate", **row})
    registry = registry_for()
    seen = _every_pair(EACH_GRAPH, registry)
    assert seen >= {"direct:", "direct:+each", "via:", "via:+each", "no:mismatch", "no:taken"}, seen
    answers = fits(Graph.from_json(EACH_GRAPH), "k.depth", registry)["graph"]
    [into_all] = [a for a in answers if (a["step"], a["port"]) == ("k2", "all")]
    assert (into_all["how"], into_all.get("each")) == ("direct", None)  # a collection taken whole
    one = fits(Graph.from_json(EACH_GRAPH), "m.one", registry)["graph"]
    [refused] = [a for a in one if (a["step"], a["port"]) == ("k2", "all")]
    assert (refused["how"], refused["reason"]) == ("no", "mismatch"), refused
    assert "single" in refused["message"]


def test_plan_refuses_collections_that_do_not_line_up(
    make_node: MakeNode, registry_for: Callable[[], Registry]
) -> None:
    for row in [
        {"id": "test.masks", "outputs": {"masks": "Mask[kind=binary]*"}},
        {"id": "test.one_mask", "outputs": {"mask": "Mask[kind=binary]"}},
        {
            "id": "test.pair",
            "inputs": {"mask": "Mask", "other": "Mask"},
            "outputs": {"mask": "Mask[kind=binary]"},
        },
        {"id": "test.whole", "inputs": {"masks": "Mask*"}, "outputs": {"mask": "Mask[kind=binary]"}},
        {"id": "test.splits", "inputs": {"mask": "Mask"}, "outputs": {"parts": "Mask[kind=binary]*"}},
    ]:
        make_node({"version": "1", "title": row["id"], "category": "evaluate", **row})
    registry = registry_for()

    def refused(nodes: dict[str, str], edges: list[tuple[str, str]]) -> list[tuple[str, str, str]]:
        with pytest.raises(GraphError) as info:
            plan(_graph({k: {"node": n} for k, n in nodes.items()}, edges), registry)
        return [(p["node"], p["port"], p["message"]) for p in info.value.problems]

    # Two steps' collections, fed item by item into one: they need not line up.
    [(node, port, message)] = refused(
        {"a": "test.masks", "b": "test.masks", "p": "test.pair"},
        [("a.masks", "p.mask"), ("b.masks", "p.other")],
    )
    assert (node, port) == ("p", "other") and "from a and from b" in message, message
    # A single value into a collection input.
    # (and, refused, the input is not connected: the second problem)
    (node, port, message), _ = refused({"o": "test.one_mask", "w": "test.whole"}, [("o.mask", "w.masks")])
    assert (node, port) == ("w", "masks") and "single Mask cannot feed a collection" in message, message
    # A for each over a node that itself gives a collection.
    [(node, port, message)] = refused({"a": "test.masks", "s": "test.splits"}, [("a.masks", "s.mask")])
    assert (node, port) == ("s", "parts") and "for each inside another" in message, message
    # One step's collection, fed directly and through a step run for each of it, lines up.
    made = plan(
        _graph(
            {"a": {"node": "test.masks"}, "e": {"node": "test.pair"}, "p": {"node": "test.pair"}},
            [("a.masks", "e.mask"), ("a.masks", "e.other"), ("a.masks", "p.mask"), ("e.mask", "p.other")],
        ),
        registry,
    )
    assert {s.id: s.each for s in made.steps} == {"a": (), "e": ("mask", "other"), "p": ("mask", "other")}
    assert "p runs once for each item of a.masks." in made.notes
    # A collection taken whole ends the for each, and starts a new origin.
    made = plan(
        _graph(
            {"a": {"node": "test.masks"}, "e": {"node": "test.pair"}, "w": {"node": "test.whole"}},
            [("a.masks", "e.mask"), ("a.masks", "e.other"), ("e.mask", "w.masks")],
        ),
        registry,
    )
    assert next(s for s in made.steps if s.id == "w").each == ()


CLICKS = {"width": 8, "height": 6, "points": [{"x": 1, "y": 2, "positive": True}], "boxes": []}


def test_a_json_param_is_held_to_its_types_shape(
    make_node: MakeNode, registry_for: Callable[[], Registry]
) -> None:
    """A json param's value is held to its port type's shape when the graph is planned, and a wrong
    one is a problem of its node and param that names the place (spec 020, AC1)."""
    make_node(
        {
            "id": "test.clicked",
            "version": "1",
            "title": "Clicked",
            "category": "segment",
            "outputs": {"text": "Text"},
            "params": {"clicks": {"type": "json", "of": "Clicks"}},
        }
    )
    registry = registry_for()
    assert plan(
        _graph({"c": {"node": "test.clicked", "params": {"clicks": CLICKS}}}, []), registry
    ).order == ["c"]
    wrong = {**CLICKS, "points": [*CLICKS["points"], {"x": 3, "y": "four", "positive": False}]}
    with pytest.raises(GraphError) as info:
        plan(_graph({"c": {"node": "test.clicked", "params": {"clicks": wrong}}}, []), registry)
    assert info.value.problems == [
        {"node": "c", "port": "", "message": "clicks: points[1].y should be number, not string"}
    ]


def test_a_file_param_names_an_import(
    make_node: MakeNode,
    registry_for: Callable[[], Registry],
    scheduler_for: Callable[..., Scheduler],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `file` param holds an import's id: the step is given that import's file. A path, or an id
    nothing was imported as, is a problem of that param, and the path is never looked at."""
    make_node(
        {
            "id": "test.reads",
            "version": "1",
            "title": "R",
            "category": "evaluate",
            "outputs": {"text": "Text"},
            "params": {"path": {"type": "file"}},
        },
        "from pathlib import Path\n"
        "def run(ctx):\n"
        "    p = ctx.path('t.json')\n"
        "    p.write_text(Path(ctx.params['path']).read_text(encoding='utf-8'), encoding='utf-8')\n"
        "    ctx.output('text', p)\n",
    )
    layout = Layout(tmp_path)
    picked = tmp_path / "Pictures" / "picked.txt"
    picked.parent.mkdir()
    picked.write_text('{"read": "the import"}', encoding="utf-8")
    ident, _name = import_file(layout, picked)
    reads = lambda value: _graph({"r": {"node": "test.reads", "params": {"path": value}}}, [])  # noqa: E731

    [step] = plan(reads(ident), registry_for(), layout.inputs).steps
    assert step.params["path"] == str(find(layout.inputs, ident))
    result = scheduler_for().run(reads(ident), lambda _event: None)
    assert result.status == "done", result.error
    assert result.outputs["r"]["text"].path.read_text(encoding="utf-8") == '{"read": "the import"}'

    real_stat = os.stat

    def guarded(path: Any, *args: Any, **kw: Any) -> Any:
        assert "Pictures" not in str(path), f"the path in the param was looked at: {path}"
        return real_stat(path, *args, **kw)

    monkeypatch.setattr(os, "stat", guarded)
    for value, needle in ((str(picked), "not a path"), ("0" * 64, "no imported file")):
        with pytest.raises(GraphError) as refused:
            plan(reads(value), registry_for(), layout.inputs)
        [problem] = refused.value.problems
        assert (problem["node"], needle in problem["message"]) == ("r", True), problem
