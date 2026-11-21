"""What a node made, held to its manifest and the port contract before it is kept (spec 020).

Every output the manifest declares must be there, in a file that exists; each facet the type has
must be stated, from its allowed values, and none may contradict the manifest; a value of a JSON
type with a shape must fit it (`ports.check_shape`). Any of these is the node's `contract`
failure. Trust flows with the value: an output that does not state its trust inherits the weakest
trust of its inputs, so a mesh built from synthetic views never comes out labelled measured.

Moved out of the scheduler, which runs a step, so that what is checked of a value grows here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from oneframe import ports
from oneframe.executors import NodeError
from oneframe.manifest import Manifest
from oneframe.node import Value

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


def check(
    manifest: Manifest, inputs: dict[str, Value], declared: dict[str, Any], key: str
) -> dict[str, Value]:
    """The values a node declared (`declared`, by port: `path`, `facets`, `meta`), checked, each
    keyed `<key>:<port>`; raises NodeError of kind `contract` at the first that breaks the rules."""
    known = ports.types()
    inherited = weakest([v.trust for v in inputs.values()])
    values: dict[str, Value] = {}
    for port, spec in manifest.outputs.items():
        row = declared.get(port)
        if row is None:
            raise NodeError("contract", f"{manifest.id} finished without its output {port!r}.")
        path = Path(row["path"])
        if not path.exists():
            raise NodeError("contract", f"{manifest.id} declared {port!r} at {path}, which does not exist.")
        facets = {k: v[0] for k, v in spec.facets.items() if len(v) == 1}
        for k, v in (row.get("facets") or {}).items():
            fixed = spec.facets.get(k)
            if fixed is not None and v not in fixed:
                raise NodeError("contract", f"{manifest.id}.{port}: {k}={v} contradicts its manifest.")
            facets[k] = v
        ptype = known[spec.type]
        for k, allowed in ptype.facets.items():
            if k not in facets:
                raise NodeError("contract", f"{manifest.id}.{port} did not say its {k}.")
            if facets[k] not in allowed:
                raise NodeError(
                    "contract", f"{manifest.id}.{port}: {k}={facets[k]} is not a {spec.type} value."
                )
        _shaped(manifest, port, spec.type, path)
        values[port] = Value(
            spec.type, path, facets, row.get("meta"), spec.trust or inherited, key=f"{key}:{port}"
        )
    return values
