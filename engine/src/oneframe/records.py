"""Records: a set of named fields, each with a type, as `contracts/protocol.json` declares its
events and answers, and `contracts/port-types.json` the shape of a JSON value (spec 020). One
grammar, read and checked here and nowhere else: `protocol.py` and `ports.py` both use it.

A record is {with, open, fields}: `with` names shapes whose fields it has too ({shape, optional}
when they may all be absent), `open` lets it hold fields of its own beside those declared, and
each field is {type, of, optional, null, page, doc}. A type is string, integer, number, boolean,
object, array, a shape's name, or a list of these for either; `of` is the type of each item of an
array or each value of an object.

Reading adds each problem to a list and never raises; checking a value adds each problem found to
`out`, naming its place in the value (`points[2].x`). Standard library only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PRIMITIVES = frozenset({"string", "integer", "number", "boolean", "object", "array"})
FIELD_KEYS = frozenset({"type", "of", "optional", "null", "page", "doc"})
RECORD_KEYS = frozenset({"doc", "with", "open", "fields"})


@dataclass(frozen=True)
class Type:
    """A field's type: one name or several (either may do), what each item or value is, and null."""

    names: tuple[str, ...]
    of: Type | None = None
    null: bool = False


@dataclass(frozen=True)
class Field:
    type: Type
    optional: bool = False
    page: bool = True  # False: it never reaches the page


@dataclass(frozen=True)
class Record:
    """An event's, a shape's, a method's params' or its answer's fields, its shapes' merged in."""

    fields: dict[str, Field]
    open: bool = False


def read_type(raw: Any, shapes: dict[str, Any], where: str, problems: list[str]) -> Type:
    row = {"type": raw} if isinstance(raw, str) else raw
    if not isinstance(row, dict):
        problems.append(f"{where}: a type should be a name or an object")
        return Type(("object",))
    names = row.get("type")
    listed = (names,) if isinstance(names, str) else tuple(names) if isinstance(names, list) else ()
    if not listed or not all(isinstance(n, str) for n in listed):
        problems.append(f"{where}: type should be a name or a list of names")
        return Type(("object",))
    for name in listed:
        if name not in PRIMITIVES and name not in shapes:
            problems.append(f"{where}: {name!r} is neither a type nor a shape")
    of = row.get("of")
    if of is not None and not {"object", "array"} & set(listed):
        problems.append(f"{where}: only an object or an array has `of`")
    inner = read_type(of, shapes, f"{where}.of", problems) if of is not None else None
    return Type(listed, inner, row.get("null") is True)


def read_field(raw: Any, shapes: dict[str, Any], where: str, problems: list[str]) -> Field:
    if not isinstance(raw, dict):
        problems.append(f"{where}: a field should be an object")
        return Field(Type(("object",)))
    problems += [f"{where}: unknown key {k!r}" for k in sorted(set(raw) - FIELD_KEYS)]
    return Field(
        read_type(raw, shapes, where, problems), raw.get("optional") is True, raw.get("page") is not False
    )


def read_record(
    raw: Any, shapes: dict[str, Any], where: str, problems: list[str], keys: frozenset[str] = RECORD_KEYS
) -> Record:
    """A record's own fields, after those of the shapes it is `with`, each of those optional when
    the record says so."""
    if not isinstance(raw, dict) or not isinstance(raw.get("fields"), dict):
        problems.append(f"{where}: should be an object with `fields`")
        return Record({})
    problems += [f"{where}: unknown key {k!r}" for k in sorted(set(raw) - keys)]
    merged: dict[str, Field] = {}
    for entry in raw.get("with") or []:
        name, optional = (
            (entry, False) if isinstance(entry, str) else (entry.get("shape"), entry.get("optional"))
        )
        shape = shapes.get(name) if isinstance(name, str) else None
        if shape is None or name == where.removeprefix("shapes."):
            problems.append(f"{where}: with {name!r}, which is not another shape")
            continue
        for key, found in read_record(shape, shapes, f"shapes.{name}", []).fields.items():
            merged[key] = Field(found.type, found.optional or optional is True, found.page)
    for key, row in raw["fields"].items():
        merged[key] = read_field(row, shapes, f"{where}.{key}", problems)
    return Record(merged, raw.get("open") is True)


# -- checking -------------------------------------------------------------------------------------


def kind_of(value: Any) -> str:
    return {
        bool: "boolean",
        int: "integer",
        float: "number",
        tuple: "array",
        str: "string",
        list: "array",
        dict: "object",
    }.get(type(value), type(value).__name__)


def _is(name: str, value: Any) -> bool:
    if name == "string":
        return isinstance(value, str)
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if name == "boolean":
        return isinstance(value, bool)
    if name == "array":
        return isinstance(value, list | tuple)  # json writes a tuple as an array
    return isinstance(value, dict)  # object, or a shape


def check_value(
    shapes: Mapping[str, Record], kind: Type, value: Any, name: str, where: str, out: list[str]
) -> None:
    if value is None:
        if not kind.null:
            out.append(f"{name}: {where} should be {' or '.join(kind.names)}, not null")
        return
    tried: list[list[str]] = []
    for one in kind.names:
        found: list[str] = []
        if not _is(one, value):
            found.append(f"{name}: {where} should be {' or '.join(kind.names)}, not {kind_of(value)}")
        elif one in shapes and isinstance(value, dict):
            check_fields(shapes, shapes[one], value, name, where, found)
        elif kind.of is not None and isinstance(value, dict):
            for key, item in value.items():
                check_value(shapes, kind.of, item, name, f"{where}.{key}", found)
        elif kind.of is not None and isinstance(value, list | tuple):
            for index, item in enumerate(value):
                check_value(shapes, kind.of, item, name, f"{where}[{index}]", found)
        if not found:
            return
        tried.append(found)
    out += min(tried, key=len)  # the type it came closest to


def check_fields(
    shapes: Mapping[str, Record],
    record: Record,
    value: dict[str, Any],
    name: str,
    where: str,
    out: list[str],
    beside: str | None = None,
) -> None:
    """`beside`: a key that is not a field (an event's own `event`)."""
    for key, spec in record.fields.items():
        path = f"{where}.{key}" if where else key
        if key not in value:
            if not spec.optional:
                out.append(f"{name}: no field {path}")
            continue
        check_value(shapes, spec.type, value[key], name, path, out)
    if not record.open:
        out += [
            f"{name}: undeclared field {f'{where}.{k}' if where else k}"
            for k in value
            if k not in record.fields and k != beside
        ]
