from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from conftest import MakeNode

from oneframe.graph import Graph, GraphError, plan
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
