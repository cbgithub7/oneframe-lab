"""What a node made, held to its manifest and the port contract before it is kept (spec 020).

Every output the manifest declares must be there, in a file that exists; each facet the type has
must be stated, from its allowed values, and none may contradict the manifest; a value of a JSON
type with a shape must fit it (`ports.check_shape`). Any of these is the node's `contract`
failure. A collection port's items are each checked so, and the collections one node gives have
one length (spec 021). Trust flows with the value: an output that does not state its trust
inherits the weakest trust of its inputs, so a mesh built from synthetic views never comes out
labelled measured.

Moved out of the scheduler, which runs a step, so that what is checked of a value grows here.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from oneframe import ports
from oneframe.cache import Held
from oneframe.executors import NodeError
from oneframe.manifest import Manifest
from oneframe.node import Value
from oneframe.ports import PortSpec

TRUST_ORDER = {"measured": 0, "predicted": 1, "synthetic": 2}


def weakest(trusts: list[str | None]) -> str | None:
    """The weakest of these trusts, or None when none is known."""
    known = [t for t in trusts if t in TRUST_ORDER]
    return max(known, key=lambda t: TRUST_ORDER[t]) if known else None


def _shaped(manifest: Manifest, port: str, type_name: str, path: Path) -> None:
    """A value of a type with a shape fits it, or the node broke its contract."""
    if ports.types()[type_name].shape is None:
        return
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        raise NodeError(
            "contract", f"{manifest.id}.{port} is a {type_name}, and its file is not JSON: {exc}"
        ) from exc
    problems = ports.check_shape(type_name, value, port)
    if problems:
        raise NodeError(
            "contract", f"{manifest.id}.{port} does not fit {type_name}'s shape: {'; '.join(problems)}."
        )


def flat(values: Iterable[Held]) -> list[Value]:
    """Every value in these, a collection's items in order."""
    return [one for held in values for one in (held if isinstance(held, list) else [held])]


def _value(
    manifest: Manifest, spec: PortSpec, row: Any, where: str, key: str, inherited: str | None
) -> Value:
    """One value a node declared (`row`: `path`, `facets`, `meta`) at `where` (`port`, or
    `port[i]` for an item), checked, keyed `key`."""
    if not isinstance(row, dict) or "path" not in row:
        raise NodeError("contract", f"{manifest.id}.{where} was declared without a file.")
    path = Path(row["path"])
    if not path.exists():
        raise NodeError("contract", f"{manifest.id} declared {where!r} at {path}, which does not exist.")
    facets = {k: v[0] for k, v in spec.facets.items() if len(v) == 1}
    for k, v in (row.get("facets") or {}).items():
        fixed = spec.facets.get(k)
        if fixed is not None and v not in fixed:
            raise NodeError("contract", f"{manifest.id}.{where}: {k}={v} contradicts its manifest.")
        facets[k] = v
    ptype = ports.types()[spec.type]
    for k, allowed in ptype.facets.items():
        if k not in facets:
            raise NodeError("contract", f"{manifest.id}.{where} did not say its {k}.")
        if facets[k] not in allowed:
            raise NodeError("contract", f"{manifest.id}.{where}: {k}={facets[k]} is not a {spec.type} value.")
    _shaped(manifest, where, spec.type, path)
    return Value(spec.type, path, facets, row.get("meta"), spec.trust or inherited, key=key)


def check(manifest: Manifest, inputs: dict[str, Held], declared: dict[str, Any], key: str) -> dict[str, Held]:
    """The values a node declared (`declared`, by port: `path`, `facets`, `meta`, or a list of
    them for a collection port), checked, each keyed `<key>:<port>`, and a collection's items
    `<key>:<port>:<index>`; raises NodeError of kind `contract` at the first that breaks the rules.
    An item is checked as a single value is, and the collections one node gives have one length."""
    inherited = weakest([v.trust for v in flat(inputs.values())])
    values: dict[str, Held] = {}
    for port, spec in manifest.outputs.items():
        row = declared.get(port)
        if not spec.many:
            if row is None:
                raise NodeError("contract", f"{manifest.id} finished without its output {port!r}.")
            if isinstance(row, list):
                raise NodeError(
                    "contract", f"{manifest.id}.{port} is a single {spec.type}, given with ctx.output_item."
                )
            values[port] = _value(manifest, spec, row, port, f"{key}:{port}", inherited)
            continue
        if row is not None and not isinstance(row, list):
            raise NodeError(
                "contract",
                f"{manifest.id}.{port} is a collection of {spec.type}, given with ctx.output: "
                "its items are given with ctx.output_item.",
            )
        values[port] = [
            _value(manifest, spec, item, f"{port}[{i}]", f"{key}:{port}:{i}", inherited)
            for i, item in enumerate(row or [])
        ]
    lengths = {port: len(held) for port, held in values.items() if isinstance(held, list)}
    if len(set(lengths.values())) > 1:
        told = ", ".join(f"{port} {n}" for port, n in lengths.items())
        raise NodeError("contract", f"{manifest.id} gave collections of different lengths: {told}.")
    return values
