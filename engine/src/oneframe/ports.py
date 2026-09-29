"""Port types and the rule for connecting them.

A port is written `Type[facet=value, facet=a|b]`. On an output, each facet named has one value:
what the node always produces. On an input, a facet lists what the node accepts; a facet an
input does not name is accepted with any value. An edge is legal when the output's type matches
and every facet the input names is satisfied.

An output that leaves a facet out is saying "it depends": the node sets it on the value when it
runs (a depth model with a metric and a relative mode, chosen by a parameter). Such an edge is
accepted at validation if some value could satisfy the input, and checked again on the real value
before the consumer runs, so the rule still holds.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from oneframe import CONTRACTS_DIR


class PortError(ValueError):
    """A port spec names a type or facet the contract does not have, or is malformed."""


@dataclass(frozen=True)
class PortType:
    name: str
    doc: str
    carrier: str
    facets: dict[str, tuple[str, ...]]


@dataclass(frozen=True)
class PortSpec:
    """One port as a manifest declares it."""

    type: str
    facets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    optional: bool = False
    doc: str = ""
    trust: str | None = None

    def text(self) -> str:
        if not self.facets:
            return self.type
        inner = ", ".join(f"{k}={'|'.join(v)}" for k, v in sorted(self.facets.items()))
        return f"{self.type}[{inner}]"


TRUST = ("measured", "predicted", "synthetic")

_SPEC = re.compile(r"^\s*(?P<type>[A-Za-z][A-Za-z0-9]*)\s*(?:\[(?P<facets>[^\]]*)\])?\s*$")


@cache
def types(path: Path | None = None) -> dict[str, PortType]:
    source = path or CONTRACTS_DIR / "port-types.json"
    data = json.loads(source.read_text(encoding="utf-8"))
    out: dict[str, PortType] = {}
    for name, row in data["types"].items():
        out[name] = PortType(
            name=name,
            doc=row.get("doc", ""),
            carrier=row.get("carrier", "json"),
            facets={k: tuple(v) for k, v in (row.get("facets") or {}).items()},
        )
    return out


def parse(spec: str | dict[str, Any], known: dict[str, PortType] | None = None) -> PortSpec:
    """Parse the short string form or the object form ({"type": ..., "optional": ..., "doc": ...})."""
    known = known if known is not None else types()
    extra: dict[str, Any] = {}
    if isinstance(spec, dict):
        extra = dict(spec)
        text = extra.pop("type", None)
        if not isinstance(text, str):
            raise PortError(f"A port in object form needs a 'type' string: {spec!r}")
        unknown = set(extra) - {"optional", "doc", "trust"}
        if unknown:
            raise PortError(f"Unknown port keys {sorted(unknown)} in {spec!r}")
    else:
        text = spec
    match = _SPEC.match(text)
    if not match:
        raise PortError(f"Cannot read port spec {text!r}; expected Type[facet=value, ...].")
    name = match.group("type")
    if name not in known:
        raise PortError(f"Unknown port type {name!r}. Known: {', '.join(sorted(known))}.")
    ptype = known[name]
    facets: dict[str, tuple[str, ...]] = {}
    for part in filter(None, (p.strip() for p in (match.group("facets") or "").split(","))):
        key, sep, value = part.partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value:
            raise PortError(f"Facet {part!r} in {text!r} should read facet=value.")
        if key not in ptype.facets:
            raise PortError(f"{name} has no facet {key!r}. Its facets: {', '.join(ptype.facets) or 'none'}.")
        values = tuple(v.strip() for v in value.split("|"))
        bad = [v for v in values if v not in ptype.facets[key]]
        if bad:
            raise PortError(
                f"{name}.{key} cannot be {', '.join(bad)}; allowed: {', '.join(ptype.facets[key])}."
            )
        if key in facets:
            raise PortError(f"Facet {key!r} is named twice in {text!r}.")
        facets[key] = values
    trust = extra.get("trust")
    if trust is not None and trust not in TRUST:
        raise PortError(f"trust must be one of {', '.join(TRUST)}, not {trust!r}.")
    return PortSpec(
        type=name,
        facets=facets,
        optional=bool(extra.get("optional", False)),
        doc=str(extra.get("doc", "")),
        trust=trust,
    )


def mismatch(output: PortSpec, accepts: PortSpec) -> str | None:
    """Why an output cannot feed an input, or None when it can (possibly after its value is known)."""
    if output.type != accepts.type:
        return f"{output.type} cannot connect to {accepts.type}"
    for key, allowed in accepts.facets.items():
        produced = output.facets.get(key)
        if produced is None:
            continue  # decided at run time; checked on the value
        if not set(produced) & set(allowed):
            return f"{output.type}.{key} is {'|'.join(produced)} but the input needs {'|'.join(allowed)}"
    return None


def value_mismatch(facets: dict[str, str], accepts: PortSpec) -> str | None:
    """The same rule applied to a real value, whose facets each have exactly one value."""
    for key, allowed in accepts.facets.items():
        have = facets.get(key)
        if have is None:
            return f"the value does not say its {key}, and the input needs {'|'.join(allowed)}"
        if have not in allowed:
            return f"{accepts.type}.{key} is {have} but the input needs {'|'.join(allowed)}"
    return None
