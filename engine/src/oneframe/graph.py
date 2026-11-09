"""Graphs (recipes): which nodes, with which parameters, connected how.

A recipe is plain JSON so it can be saved, shared and edited by hand:

    {
      "version": 1,
      "title": "Photo to point map",
      "nodes": {
        "photo": {"node": "source.image", "params": {"path": "<the photo's import id>"}},
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
from pathlib import Path
from typing import Any

from oneframe import ports
from oneframe.errors import Failure, failure
from oneframe.inputs import find
from oneframe.manifest import Manifest, check_param
from oneframe.ports import PortSpec
from oneframe.registry import Registry

RECIPE_VERSION = 1


class GraphError(Failure, ValueError):
    """The graph cannot run: kind `graph`, and every problem, each against its node and port."""

    kind = "graph"

    def __init__(self, problems: list[dict[str, str]]):
        self.problems = problems
        super().__init__(
            "; ".join(f"{p.get('node', '')}.{p.get('port', '')}: {p['message']}" for p in problems)
        )

    def to_json(self, **extra: Any) -> dict[str, Any]:
        return failure("graph", "The graph cannot run.", problems=self.problems, **extra)


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
    # The page's own fields (a card's place, which outputs are shown): kept as they are, never read.
    page: dict[str, Any] | None = None

    def to_json(self) -> dict[str, Any]:
        row: dict[str, Any] = {"node": self.node, "params": self.params}
        if self.label:
            row["label"] = self.label
        if self.page is not None:
            row["page"] = self.page
        return row


def _object(value: Any, where: str, problems: list[dict[str, str]], node: str = "") -> dict[str, Any] | None:
    """`value` when it is a JSON object, None when it is missing; anything else is a problem."""
    if value is None or isinstance(value, dict):
        return value
    problems.append({"node": node, "port": "", "message": f"{where} should be an object"})
    return None


def _node(key: str, row: Any, problems: list[dict[str, str]]) -> GraphNode | None:
    if not isinstance(row, dict):
        problems.append({"node": key, "port": "", "message": "a node should be an object"})
        return None
    node, label = row.get("node"), row.get("label", "")
    if not isinstance(node, str) or not node:
        problems.append({"node": key, "port": "", "message": "a node should name its node, as text"})
    if not isinstance(label, str):
        problems.append({"node": key, "port": "", "message": "label should be text"})
    params = _object(row.get("params"), "params", problems, key)
    page = _object(row.get("page"), "page", problems, key)
    if not isinstance(node, str) or not isinstance(label, str):
        return None
    return GraphNode(node=node, params=dict(params or {}), label=label, page=page)


def _edge(row: Any, problems: list[dict[str, str]]) -> Edge | None:
    ends = [row.get(end) for end in ("from", "to")] if isinstance(row, dict) else []
    if len(ends) != 2 or not all(isinstance(end, str) and "." in end for end in ends):
        problems.append(
            {"node": "", "port": "", "message": f"an edge should read from and to node.port: {row!r}"}
        )
        return None
    return Edge.from_json(row)


@dataclass
class Graph:
    nodes: dict[str, GraphNode]
    edges: list[Edge]
    title: str = ""
    page: dict[str, Any] | None = None  # the page's own fields, as on a node

    @classmethod
    def from_json(cls, data: Any) -> Graph:
        """A recipe from its JSON. A recipe of another version, or one of the wrong shape, raises
        GraphError with every problem, never a KeyError."""
        if not isinstance(data, dict):
            raise GraphError([{"node": "", "port": "", "message": "a graph should be a JSON object"}])
        version = data.get("version", RECIPE_VERSION)
        if isinstance(version, bool) or version != RECIPE_VERSION:
            raise GraphError([{"message": f"recipe version {version} is not {RECIPE_VERSION}"}])
        problems: list[dict[str, str]] = []
        title = data.get("title", "")
        if not isinstance(title, str):
            problems.append({"node": "", "port": "", "message": "title should be text"})
        rows = _object(data.get("nodes"), "nodes", problems) or {}
        nodes = {key: found for key, row in rows.items() if (found := _node(key, row, problems))}
        listed = data.get("edges") or []
        if not isinstance(listed, list):
            problems.append({"node": "", "port": "", "message": "edges should be a list"})
            listed = []
        edges = [found for row in listed if (found := _edge(row, problems))]
        page = _object(data.get("page"), "page", problems)
        if problems:
            raise GraphError(problems)
        return cls(nodes=nodes, edges=edges, title=str(title), page=page)

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "version": RECIPE_VERSION,
            "title": self.title,
            "nodes": {k: n.to_json() for k, n in self.nodes.items()},
            "edges": [e.to_json() for e in self.edges],
        }
        if self.page is not None:
            data["page"] = self.page
        return data


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
    # The edges accepted only if the value's facets agree: the output leaves open a facet the input
    # names, and the scheduler checks the value when it arrives.
    depends: list[dict[str, str]] = field(default_factory=list)

    @property
    def order(self) -> list[str]:
        return [s.id for s in self.steps]


@dataclass(frozen=True)
class Connection:
    """How an output can feed an input: `direct`; `via` a converter the plan inserts; `depends`,
    directly once the value says the facets the output leaves open; or `no`, and why."""

    how: str
    converter: Manifest | None = None
    reason: str = ""  # for `no`: mismatch, taken (the input is fed) or cycle
    message: str = ""

    def to_json(self) -> dict[str, str]:
        row = {"how": self.how}
        if self.converter is not None:
            row["converter"] = self.converter.id
        if self.reason:
            row |= {"reason": self.reason, "message": self.message}
        return row


def connect(output: PortSpec, accepts: PortSpec, converters: list[Manifest]) -> Connection:
    """The one rule for an edge, which `plan()` and `fits()` both follow."""
    why = ports.mismatch(output, accepts)
    if why is None:
        open_facet = any(key not in output.facets for key in accepts.facets)
        return Connection("depends" if open_facet else "direct")
    for converter in converters:
        takes, gives = next(iter(converter.inputs.values())), next(iter(converter.outputs.values()))
        if ports.mismatch(output, takes) is None and ports.mismatch(gives, accepts) is None:
            return Connection("via", converter)
    return Connection("no", reason="mismatch", message=why)


def step_params(
    manifest: Manifest, given: dict[str, Any], imports: Path | None = None
) -> tuple[dict[str, Any], list[str]]:
    """A step's params: what the graph gives, the defaults for the rest, and every problem. A
    `file` param names an import, found in `imports` (the layout's `inputs`): the step is given
    that file, so the node reads a file, and the graph holds no path."""
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
        elif param.type == "file":
            found = find(imports, value) if imports is not None else None
            if found is None:
                problems.append(f"{name}: no imported file has the id {value}")
            else:
                value = str(found)
        params[name] = value
    problems += [f"{manifest.id} has no parameter {name}" for name in given if name not in manifest.params]
    return params, problems


def plan(graph: Graph, registry: Registry, imports: Path | None = None) -> Plan:
    """The order to run `graph` in, its converters inserted; every problem at once as GraphError.
    `imports`: the folder a `file` param's import is found in (the layout's `inputs`)."""
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
        params, wrong = step_params(manifest, gnode.params, imports)
        for why in wrong:
            problem(key, why)
        steps[key] = Step(
            id=key, manifest=manifest, params=params, inputs={}, explicit=frozenset(gnode.params)
        )

    converters = registry.converters()
    depends: list[dict[str, str]] = []
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
        found = connect(out_spec, in_spec, converters)
        if found.how in ("direct", "depends"):
            dst.inputs[edge.dst_port] = (edge.src, edge.src_port)
            if found.how == "depends":
                depends.append(edge.to_json())
            continue
        bridge = found.converter
        if bridge is None:
            problem(edge.dst, found.message, edge.dst_port)
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
    return Plan(steps=[steps[sid] for sid in order], notes=notes, depends=depends)


def _reaches(graph: Graph, start: str, backwards: bool = False) -> set[str]:
    """Every node a path of edges leads to from `start` (`backwards`: leads from to `start`),
    `start` included."""
    after: dict[str, set[str]] = {}
    for edge in graph.edges:
        src, dst = (edge.dst, edge.src) if backwards else (edge.src, edge.dst)
        after.setdefault(src, set()).add(dst)
    seen, todo = {start}, [start]
    while todo:
        for nxt in after.get(todo.pop(), ()):
            if nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen


def fits(graph: Graph, port: str, registry: Registry) -> dict[str, Any]:
    """What `port` (`step.port`, an output or an input of a node in the graph) can connect to: each
    opposite port of the graph's other nodes (`graph`) and of every installed node a person could
    add (`add`), each with how, by `connect`. An input already fed is `no` (taken), and an edge
    that would close a cycle `no` (cycle), as `plan()` would refuse them."""
    key, _, name = port.partition(".")
    gnode = graph.nodes.get(key)
    manifest = registry.nodes.get(gnode.node) if gnode is not None else None
    if manifest is None or (name not in manifest.outputs and name not in manifest.inputs):
        raise GraphError([{"node": key, "port": name, "message": f"{port} is no port of an installed node"}])
    converters = registry.converters()
    fed = {(e.dst, e.dst_port): f"{e.src}.{e.src_port}" for e in graph.edges}
    downstream = _reaches(graph, key)  # an edge into `key` from any of these closes a cycle
    upstream = _reaches(graph, key, backwards=True)  # and one out of `key` into any of these
    found: dict[str, list[dict[str, Any]]] = {"graph": [], "add": []}

    def answer(node: Manifest, other: str, side: str, step: str | None) -> dict[str, Any]:
        if side == "input":  # `port` is the output, `other` an input
            taken = fed.get((step, other)) if step is not None else None
            out_spec, in_spec, cycle = manifest.outputs[name], node.inputs[other], step in upstream
        else:
            taken = fed.get((key, name))
            out_spec, in_spec, cycle = node.outputs[other], manifest.inputs[name], step in downstream
        if taken is not None:
            how = Connection("no", reason="taken", message=f"the input is fed by {taken}")
        elif cycle:
            how = Connection("no", reason="cycle", message="the edge would close a cycle")
        else:
            how = connect(out_spec, in_spec, converters)
        place = {"step": step} if step is not None else {}
        return {**place, "node": node.id, "port": other, "side": side, **how.to_json()}

    def each(node: Manifest, step: str | None, into: list[dict[str, Any]]) -> None:
        if name in manifest.outputs:
            into += [answer(node, other, "input", step) for other in node.inputs]
        if name in manifest.inputs:
            into += [answer(node, other, "output", step) for other in node.outputs]

    for other_key, other in graph.nodes.items():
        installed = registry.nodes.get(other.node)
        if other_key != key and installed is not None:
            each(installed, other_key, found["graph"])
    for node in sorted(registry.nodes.values(), key=lambda m: m.id):
        each(node, None, found["add"])
    return {"port": port, **found}
