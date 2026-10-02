"""Graphs (recipes): which nodes, with which parameters, connected how.

A recipe is plain JSON so it can be saved, shared and edited by hand:

    {
      "version": 1,
      "title": "Photo to point map",
      "nodes": {
        "photo": {"node": "source.image", "params": {"path": "C:/photos/chair.jpg"}},
        "depth": {"node": "depth.moge2"},
        "points": {"node": "convert.depth_to_points"}
      },
      "edges": [
        {"from": "photo.image", "to": "depth.image"},
        {"from": "depth.depth", "to": "points.depth"},
        {"from": "depth.camera", "to": "points.camera"}
      ]
    }

`plan()` checks a graph against the installed nodes and returns the order to run it in. Every
problem is reported at once, each naming its node and port. Where two ports differ only in facets
and a converter node bridges them, the converter is inserted and the plan says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from oneframe import ports
from oneframe.manifest import Manifest, check_param
from oneframe.registry import Registry

RECIPE_VERSION = 1


class GraphError(ValueError):
    def __init__(self, problems: list[dict[str, str]]):
        self.problems = problems
        super().__init__(
            "; ".join(f"{p.get('node', '')}.{p.get('port', '')}: {p['message']}" for p in problems)
        )


@dataclass(frozen=True)
class Edge:
    src: str
    src_port: str
    dst: str
    dst_port: str

    @classmethod
    def from_json(cls, row: dict[str, str]) -> Edge:
        src, _, src_port = str(row["from"]).partition(".")
        dst, _, dst_port = str(row["to"]).partition(".")
        return cls(src, src_port, dst, dst_port)

    def to_json(self) -> dict[str, str]:
        return {"from": f"{self.src}.{self.src_port}", "to": f"{self.dst}.{self.dst_port}"}


@dataclass
class GraphNode:
    node: str
    params: dict[str, Any] = field(default_factory=dict)
    label: str = ""


@dataclass
class Graph:
    nodes: dict[str, GraphNode]
    edges: list[Edge]
    title: str = ""

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> Graph:
        version = data.get("version", RECIPE_VERSION)
        if version != RECIPE_VERSION:
            raise GraphError([{"message": f"recipe version {version} is not {RECIPE_VERSION}"}])
        nodes = {
            key: GraphNode(
                node=str(row["node"]), params=dict(row.get("params") or {}), label=str(row.get("label", ""))
            )
            for key, row in (data.get("nodes") or {}).items()
        }
        edges = [Edge.from_json(row) for row in data.get("edges") or []]
        return cls(nodes=nodes, edges=edges, title=str(data.get("title", "")))

    def to_json(self) -> dict[str, Any]:
        return {
            "version": RECIPE_VERSION,
            "title": self.title,
            "nodes": {
                k: {"node": n.node, "params": n.params, **({"label": n.label} if n.label else {})}
                for k, n in self.nodes.items()
            },
            "edges": [e.to_json() for e in self.edges],
        }


@dataclass
class Step:
    """One node of the plan, ready to run."""

    id: str
    manifest: Manifest
    params: dict[str, Any]
    inputs: dict[str, tuple[str, str]]  # input port -> (step id, output port)
    inserted: bool = False
    # The params the graph sets. The fit never changes them (spec 002); the rest are defaults.
    explicit: frozenset[str] = frozenset()


@dataclass
class Plan:
    steps: list[Step]
    notes: list[str]

    @property
    def order(self) -> list[str]:
        return [s.id for s in self.steps]


def step_params(manifest: Manifest, given: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """A step's params: what the graph gives, the defaults for the rest, and every problem."""
    params: dict[str, Any] = {}
    problems: list[str] = []
    for name, param in manifest.params.items():
        value = given.get(name, param.default)
        if value is None:
            problems.append(f"parameter {name} needs a value")
            continue
        why = check_param(name, param, value)
        if why:
            problems.append(why)
        params[name] = value
    problems += [f"{manifest.id} has no parameter {name}" for name in given if name not in manifest.params]
    return params, problems


def plan(graph: Graph, registry: Registry) -> Plan:
    problems: list[dict[str, str]] = []
    notes: list[str] = []

    def problem(node: str, message: str, port: str = "") -> None:
        problems.append({"node": node, "port": port, "message": message})

    steps: dict[str, Step] = {}
    for key, gnode in graph.nodes.items():
        if "." in key or "~" in key or not key:
            problem(key, "node names in a graph cannot contain . or ~")
            continue
        manifest = registry.nodes.get(gnode.node)
        if manifest is None:
            problem(key, f"no node called {gnode.node!r} is installed")
            continue
        params, wrong = step_params(manifest, gnode.params)
        for why in wrong:
            problem(key, why)
        steps[key] = Step(
            id=key, manifest=manifest, params=params, inputs={}, explicit=frozenset(gnode.params)
        )

    converters = registry.converters()
    for edge in graph.edges:
        src, dst = steps.get(edge.src), steps.get(edge.dst)
        if edge.src not in graph.nodes:
            problem(edge.src, "an edge starts at a node that is not in the graph")
        if edge.dst not in graph.nodes:
            problem(edge.dst, "an edge ends at a node that is not in the graph")
        if src is None or dst is None:
            continue
        out_spec = src.manifest.outputs.get(edge.src_port)
        in_spec = dst.manifest.inputs.get(edge.dst_port)
        if out_spec is None:
            problem(edge.src, f"{src.manifest.id} has no output {edge.src_port!r}", edge.src_port)
            continue
        if in_spec is None:
            problem(edge.dst, f"{dst.manifest.id} has no input {edge.dst_port!r}", edge.dst_port)
            continue
        if edge.dst_port in dst.inputs:
            problem(edge.dst, "two edges feed the same input", edge.dst_port)
            continue
        why = ports.mismatch(out_spec, in_spec)
        if why is None:
            dst.inputs[edge.dst_port] = (edge.src, edge.src_port)
            continue
        bridge = next(
            (
                c
                for c in converters
                if ports.mismatch(out_spec, next(iter(c.inputs.values()))) is None
                and ports.mismatch(next(iter(c.outputs.values())), in_spec) is None
            ),
            None,
        )
        if bridge is None:
            problem(edge.dst, why, edge.dst_port)
            continue
        conv_id = f"{edge.dst}~{edge.dst_port}~{bridge.id}"
        conv_in = next(iter(bridge.inputs))
        conv_out = next(iter(bridge.outputs))
        steps[conv_id] = Step(
            id=conv_id,
            manifest=bridge,
            params={k: p.default for k, p in bridge.params.items()},
            inputs={conv_in: (edge.src, edge.src_port)},
            inserted=True,
        )
        dst.inputs[edge.dst_port] = (conv_id, conv_out)
        notes.append(
            f"Inserted {bridge.title or bridge.id} between {edge.src}.{edge.src_port} "
            f"and {edge.dst}.{edge.dst_port}."
        )

    for step in list(steps.values()):
        for name, spec in step.manifest.inputs.items():
            if name not in step.inputs and not spec.optional:
                problem(step.id, f"input {name} ({spec.text()}) is not connected", name)

    # Kahn's algorithm: the order is stable (graph order among ready nodes) so the same recipe
    # always runs the same way.
    ready_order = list(steps)
    remaining = {sid: {src for src, _ in s.inputs.values()} for sid, s in steps.items()}
    order: list[str] = []
    while True:
        ready = [sid for sid in ready_order if sid in remaining and not remaining[sid]]
        if not ready:
            break
        for sid in ready:
            order.append(sid)
            del remaining[sid]
            for deps in remaining.values():
                deps.discard(sid)
    if remaining:
        problem(sorted(remaining)[0], "the graph has a cycle through " + ", ".join(sorted(remaining)))

    if problems:
        raise GraphError(problems)
    return Plan(steps=[steps[sid] for sid in order], notes=notes)
