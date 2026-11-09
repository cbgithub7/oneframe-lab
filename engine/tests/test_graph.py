from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from typing import Any

import pytest
from conftest import MakeNode

from oneframe.graph import Graph, GraphError, fits, plan
from oneframe.registry import Registry


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
            "category": "test",
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
            "category": "test",
            "outputs": {"depth": "Depth[kind=affine_invariant_disparity, measure=z]"},
        }
    )
    make_node(
        {
            "id": "test.needs_metric",
            "version": "1",
            "title": "M",
            "category": "test",
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
    if inserted:
        return {"how": "via", "converter": inserted[0]}
    return {"how": "depends" if {"from": src, "to": dst} in made.depends else "direct"}


def _agrees(answer: dict[str, Any], planned: dict[str, Any], edge: str) -> None:
    assert answer["how"] == planned["how"], f"{edge}: fits says {answer}, plan {planned}"
    if answer["how"] == "via":
        assert answer["converter"] == planned["converter"], edge
    if answer["how"] == "no":
        assert (answer["reason"] == "taken") == planned["taken"], f"{edge}: {answer}"
        if planned["cycle"]:
            assert answer["reason"] == "cycle", f"{edge}: {answer}"


def test_what_fits_agrees_with_plan(make_node: MakeNode, registry_for: Callable[[], Registry]) -> None:
    for row in FITS_NODES:
        make_node({"version": "1", "title": row["id"], "category": "test", **row})
    registry = registry_for()
    graph = Graph.from_json(FITS_GRAPH)
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
                _agrees(answer, _planned(FITS_GRAPH, *edge, registry), " -> ".join(edge))
                back = side(fits(graph, edge[1], registry)["graph"], "output")
                [mirror] = [a for a in back if (a["step"], a["port"]) == (key, out)]
                assert {k: mirror[k] for k in ("how", "converter", "reason") if k in mirror} == {
                    k: answer[k] for k in ("how", "converter", "reason") if k in answer
                }, edge
                seen.add(answer["how"] + ":" + answer.get("reason", ""))
            for answer in side(answers["add"], "input"):  # a new node of each kind, fed from here
                data = dict(FITS_GRAPH, nodes={**FITS_GRAPH["nodes"], "new": {"node": answer["node"]}})
                edge = (f"{key}.{out}", f"new.{answer['port']}")
                _agrees(answer, _planned(data, *edge, registry), " -> ".join(edge))
        for into in manifest.inputs:
            for answer in side(fits(graph, f"{key}.{into}", registry)["add"], "output"):  # feeding it
                data = dict(FITS_GRAPH, nodes={**FITS_GRAPH["nodes"], "new": {"node": answer["node"]}})
                edge = (f"new.{answer['port']}", f"{key}.{into}")
                _agrees(answer, _planned(data, *edge, registry), " -> ".join(edge))
    assert seen >= {"direct:", "via:", "depends:", "no:mismatch", "no:taken", "no:cycle"}, seen
    with pytest.raises(GraphError, match="no port"):
        fits(graph, "s.nope", registry)
