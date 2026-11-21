"""Port types and the rule for connecting them.

A port is written `Type[facet=value, facet=a|b]`, and a trailing `*` makes it a collection of that
type (spec 021): `Mask[kind=binary]*`, an ordered list of values, each with its own facets. On an
output, each facet named has one value: what the node always produces. On an input, a facet lists
what the node accepts; a facet an input does not name is accepted with any value. An edge is legal
when the output's type matches and every facet the input names is satisfied.

An output that leaves a facet out is saying "it depends": the node sets it on the value when it
runs (a depth model with a metric and a relative mode, chosen by a parameter). Such an edge is
accepted at validation if some value could satisfy the input, and checked again on the real value
before the consumer runs, so the rule still holds.
"""

from __future__ import annotations

import copy
import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from oneframe import CONTRACTS_DIR
from oneframe.records import Record, check_fields, kind_of, read_record


class PortError(ValueError):
    """A port spec names a type or facet the contract does not have, or is malformed."""


@dataclass(frozen=True)
class PortType:
    name: str
    doc: str
    carrier: str
    facets: dict[str, tuple[str, ...]]
    # A JSON type's shape as the contract writes it, a record in protocol.json's grammar (spec 020);
    # None for a type without one. `check_shape` holds a value to it.
    shape: dict[str, Any] | None = None


@dataclass(frozen=True)
class PortSpec:
    """One port as a manifest declares it."""

    type: str
    facets: dict[str, tuple[str, ...]] = field(default_factory=dict)
    optional: bool = False
    doc: str = ""
    trust: str | None = None
    many: bool = False  # a collection of the type, written with a trailing `*` (spec 021)

    def text(self) -> str:
        star = "*" if self.many else ""
        if not self.facets:
            return self.type + star
        inner = ", ".join(f"{k}={'|'.join(v)}" for k, v in sorted(self.facets.items()))
        return f"{self.type}[{inner}]{star}"


TRUST = ("measured", "predicted", "synthetic")

_SPEC = re.compile(r"^\s*(?P<type>[A-Za-z][A-Za-z0-9]*)\s*(?:\[(?P<facets>[^\]]*)\])?\s*(?P<many>\*)?\s*$")


@cache
def _contract(path: Path | None = None) -> dict[str, Any]:
    """`contracts/port-types.json` (or a test's copy), read once."""
    source = path or CONTRACTS_DIR / "port-types.json"
    return json.loads(source.read_text(encoding="utf-8"))


@cache
def types(path: Path | None = None) -> dict[str, PortType]:
    data = _contract(path)
    out: dict[str, PortType] = {}
    for name, row in data["types"].items():
        out[name] = PortType(
            name=name,
            doc=row.get("doc", ""),
            carrier=row.get("carrier", "json"),
            facets={k: tuple(v) for k, v in (row.get("facets") or {}).items()},
            shape=row.get("shape"),
        )
    return out


def _shared(data: dict[str, Any]) -> dict[str, Any]:
    """The records a shape may name, as the contract writes them (`shapes`, its comment left out)."""
    return {name: row for name, row in (data.get("shapes") or {}).items() if not name.startswith("_")}


@cache
def _shapes(path: Path | None = None) -> tuple[dict[str, Record], dict[str, Record]]:
    """The records shapes may name, and each JSON type's shape, read; a contract that holds a
    problem raises PortError naming every one."""
    data = _contract(path)
    raw = _shared(data)
    problems: list[str] = []
    shared = {name: read_record(row, raw, f"shapes.{name}", problems) for name, row in raw.items()}
    own: dict[str, Record] = {}
    for name, row in data["types"].items():
        if row.get("shape") is None:
            continue
        if row.get("carrier") != "json":
            problems.append(f"types.{name}: only a type carried as json has a shape")
        own[name] = read_record(row["shape"], raw, f"types.{name}.shape", problems)
    if problems:
        raise PortError(f"contracts/port-types.json: {'; '.join(problems)}")
    return shared, own


def check_shape(type_name: str, value: Any, name: str, path: Path | None = None) -> list[str]:
    """Every way `value` does not fit `type_name`'s shape, each naming its place under `name` (the
    param or the port); none for a type without a shape (spec 020)."""
    shared, own = _shapes(path)
    record = own.get(type_name)
    if record is None:
        return []
    if not isinstance(value, dict):
        return [f"{name} should be an object, not {kind_of(value)}"]
    found: list[str] = []
    check_fields(shared, record, value, name, "", found)
    return found


def _named(raw: Any, shared: dict[str, Any], into: set[str]) -> None:
    """The shared records `raw` (a record, a field or a type) names, and those they name, into
    `into`: a name stands as a field's `type` or `of`, or in a record's `with`."""
    if isinstance(raw, str):
        if raw in shared and raw not in into:
            into.add(raw)
            _named(shared[raw], shared, into)
    elif isinstance(raw, list):
        for inner in raw:
            _named(inner, shared, into)
    elif isinstance(raw, dict):
        for key in ("type", "of", "with", "shape"):
            _named(raw.get(key), shared, into)
        fields = raw.get("fields")
        if isinstance(fields, dict):
            for row in fields.values():
                _named(row, shared, into)


def contract(names: Iterable[str], path: Path | None = None) -> dict[str, dict[str, Any]]:
    """What the contract says of each type named, but its doc, by name: the part of a node's cache
    key that makes a change to a type it names run it again (spec 019). A name the contract does
    not have is left out; a manifest that names one is refused before any key is made."""
    data = _contract(path)
    rows, shared = data["types"], _shared(data)
    out: dict[str, dict[str, Any]] = {}
    for name in sorted(set(names)):
        if name not in rows:
            continue
        row = {k: copy.deepcopy(v) for k, v in rows[name].items() if k != "doc"}
        named: set[str] = set()
        _named(row.get("shape"), shared, named)
        if named:  # a change to a record its shape names changes the type too (spec 020)
            row["shapes"] = {key: copy.deepcopy(shared[key]) for key in sorted(named)}
        out[name] = row
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
    elif isinstance(spec, str):
        text = spec
    else:
        raise PortError(f"A port should be a type, such as Image, or an object with one: {spec!r}")
    match = _SPEC.match(text)
    if not match:
        raise PortError(f"Cannot read port spec {text!r}; expected Type[facet=value, ...], or that and *.")
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
        many=match.group("many") is not None,
    )


def mismatch(output: PortSpec, accepts: PortSpec) -> str | None:
    """Why an output's values cannot feed an input's, or None when they can (possibly after the value
    is known). It compares the item types: whether a collection meets a single input is the
    graph's rule (`graph.connect`)."""
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
