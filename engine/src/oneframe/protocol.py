"""What the engine says to the app and the page, held to `contracts/protocol.json` (spec 018).

The contract declares every method (its params and the fields of its answer) and every event (its
fields, each with its type and whether it may be absent or null); its `_comment` gives the grammar.
This module reads it once and checks against it:

- `event(name, **fields)` builds an event and checks it. Every event the engine's own process
  makes is built with it, never as a dict literal.
- `check_event(message)` checks one built elsewhere: what a node's child says (`stage`,
  `progress`, `ceiling`, `step_oom`), which a runtime's Python cannot check, as the scheduler
  passes it on with its step and run.
- `check_answer(method, result)` and `check_error(method, error)` check what the server answers,
  and `check_answer(method, None, stopped=True)` the `{"stopped": true}` a stop gives a request
  it took off the queue, which only a method the contract marks `queued` may get.

A problem never stops the message: it is logged once per event or method name, and kept for
`problems()`, which the test suite fails on after each test (conftest.py), then `clear()`s. What
never reaches the app is not declared: a runtime child's messages to the engine (`pid`, `done`,
`error`), the journal's own lines, the command-line tools' output (`pin.*`).

Standard library only, with `reading`, so importing it keeps the engine light.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any

from oneframe import CONTRACTS_DIR
from oneframe.reading import ProblemsError, read_object

LOGGER = logging.getLogger(__name__)

CONTRACT = CONTRACTS_DIR / "protocol.json"
FORMAT = 1
PRIMITIVES = frozenset({"string", "integer", "number", "boolean", "object", "array"})
SENDERS = frozenset({"engine", "app"})
FIELD_KEYS = frozenset({"type", "of", "optional", "null", "page", "doc"})
RECORD_KEYS = frozenset({"doc", "with", "open", "fields"})
EVENT_KEYS = RECORD_KEYS | {"by"}
METHOD_KEYS = frozenset({"doc", "by", "queued", "null", "params", "answer"})
KEPT_MAX = 1000  # problems kept for problems(): an engine that runs for days stays bounded


class ProtocolError(ProblemsError):
    """The contract cannot be read, or holds problems."""


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


@dataclass(frozen=True)
class Method:
    params: Record
    answer: Record
    by: str
    queued: bool = False
    null: bool = False


@dataclass(frozen=True)
class Contract:
    shapes: dict[str, Record]
    events: dict[str, Record]
    methods: dict[str, Method]
    senders: dict[str, tuple[str, ...]] = field(default_factory=dict)  # by event


def _type(raw: Any, shapes: dict[str, Any], where: str, problems: list[str]) -> Type:
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
    inner = _type(of, shapes, f"{where}.of", problems) if of is not None else None
    return Type(listed, inner, row.get("null") is True)


def _field(raw: Any, shapes: dict[str, Any], where: str, problems: list[str]) -> Field:
    if not isinstance(raw, dict):
        problems.append(f"{where}: a field should be an object")
        return Field(Type(("object",)))
    problems += [f"{where}: unknown key {k!r}" for k in sorted(set(raw) - FIELD_KEYS)]
    return Field(
        _type(raw, shapes, where, problems), raw.get("optional") is True, raw.get("page") is not False
    )


def _record(
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
        for key, found in _record(shape, shapes, f"shapes.{name}", []).fields.items():
            merged[key] = Field(found.type, found.optional or optional is True, found.page)
    for key, row in raw["fields"].items():
        merged[key] = _field(row, shapes, f"{where}.{key}", problems)
    return Record(merged, raw.get("open") is True)


def parse(data: dict[str, Any], source: str = "protocol.json") -> Contract:
    """The contract in `data`, with every problem in it reported at once."""
    problems: list[str] = []
    if data.get("format") != FORMAT:
        problems.append(f"format should be {FORMAT}")
    rows = {k: data.get(k) for k in ("shapes", "events", "methods")}
    for key, row in rows.items():
        if not isinstance(row, dict):
            problems.append(f"{key} should be an object")
            rows[key] = {}
    shapes_raw: dict[str, Any] = rows["shapes"]  # type: ignore[assignment]
    shapes = {name: _record(row, shapes_raw, f"shapes.{name}", problems) for name, row in shapes_raw.items()}
    events: dict[str, Record] = {}
    senders: dict[str, tuple[str, ...]] = {}
    for name, row in rows["events"].items():  # type: ignore[union-attr]
        events[name] = _record(row, shapes_raw, f"events.{name}", problems, EVENT_KEYS)
        by = row.get("by") if isinstance(row, dict) else None
        if not isinstance(by, list) or not by or not set(by) <= SENDERS:
            problems.append(f"events.{name}: by should list who sends it: {', '.join(sorted(SENDERS))}")
            by = []
        senders[name] = tuple(by)
    methods: dict[str, Method] = {}
    for name, row in rows["methods"].items():  # type: ignore[union-attr]
        where = f"methods.{name}"
        if not isinstance(row, dict):
            problems.append(f"{where}: should be an object")
            continue
        problems += [f"{where}: unknown key {k!r}" for k in sorted(set(row) - METHOD_KEYS)]
        if row.get("by") not in SENDERS:
            problems.append(f"{where}: by should say who answers it: {', '.join(sorted(SENDERS))}")
        methods[name] = Method(
            _record(row.get("params"), shapes_raw, f"{where}.params", problems),
            _record(row.get("answer"), shapes_raw, f"{where}.answer", problems),
            str(row.get("by")),
            row.get("queued") is True,
            row.get("null") is True,
        )
    if problems:
        raise ProtocolError(source, problems)
    return Contract(shapes, events, methods, senders)


@cache
def contract(path: Path | None = None) -> Contract:
    """The contract, read once."""
    source = path or CONTRACT
    data, _read = read_object(source, ProtocolError)
    return parse(data, str(source))


# -- checking -------------------------------------------------------------------------------------


def _kind(value: Any) -> str:
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


def _value(c: Contract, kind: Type, value: Any, name: str, where: str, out: list[str]) -> None:
    if value is None:
        if not kind.null:
            out.append(f"{name}: {where} should be {' or '.join(kind.names)}, not null")
        return
    tried: list[list[str]] = []
    for one in kind.names:
        found: list[str] = []
        if not _is(one, value):
            found.append(f"{name}: {where} should be {' or '.join(kind.names)}, not {_kind(value)}")
        elif one in c.shapes and isinstance(value, dict):
            _fields(c, c.shapes[one], value, name, where, found)
        elif kind.of is not None and isinstance(value, dict):
            for key, item in value.items():
                _value(c, kind.of, item, name, f"{where}.{key}", found)
        elif kind.of is not None and isinstance(value, list | tuple):
            for index, item in enumerate(value):
                _value(c, kind.of, item, name, f"{where}[{index}]", found)
        if not found:
            return
        tried.append(found)
    out += min(tried, key=len)  # the type it came closest to


def _fields(
    c: Contract, record: Record, value: dict[str, Any], name: str, where: str, out: list[str]
) -> None:
    for key, spec in record.fields.items():
        path = f"{where}.{key}" if where else key
        if key not in value:
            if not spec.optional:
                out.append(f"{name}: no field {path}")
            continue
        _value(c, spec.type, value[key], name, path, out)
    if not record.open:
        out += [
            f"{name}: undeclared field {f'{where}.{k}' if where else k}"
            for k in value
            if k not in record.fields
        ]


_lock = threading.Lock()
_kept: list[str] = []
_known: set[str] = set()
_logged: set[str] = set()


def _note(name: str, found: list[str]) -> None:
    """Keep the problems found for `name`, and log the first, once per name."""
    if not found:
        return
    with _lock:
        for problem in found:
            if problem not in _known and len(_kept) < KEPT_MAX:
                _known.add(problem)
                _kept.append(problem)
        first = name not in _logged
        _logged.add(name)
    if first:
        LOGGER.warning("protocol: %s (sent all the same)", "; ".join(found))


def check_event(message: dict[str, Any]) -> dict[str, Any]:
    """`message`, as it is, after checking it against its event's declaration."""
    name = message.get("event")
    c = contract()
    record = c.events.get(name) if isinstance(name, str) else None
    if record is None:
        _note(str(name), [f"{name!r} is not a declared event"])
        return message
    found: list[str] = []
    _fields(c, record, {k: v for k, v in message.items() if k != "event"}, str(name), "", found)
    _note(str(name), found)
    return message


def event(name: str, /, **fields: Any) -> dict[str, Any]:
    """The event `name` with these fields, checked."""
    return check_event({"event": name, **fields})


def check_answer(method: str, result: Any, *, stopped: bool = False) -> None:
    """Check the result the engine answers `method` with; with `stopped`, that the method may be
    answered `{"stopped": true}` because it waits its turn."""
    c = contract()
    declared = c.methods.get(method)
    if declared is None:
        _note(method, [f"{method!r} is not a declared method"])
        return
    found: list[str] = []
    if stopped:
        if not declared.queued:
            found.append(f"{method}: answered stopped, but it is not queued")
    elif result is None:
        if not declared.null:
            found.append(f"{method}: the answer should be an object, not null")
    elif not isinstance(result, dict):
        found.append(f"{method}: the answer should be an object, not {_kind(result)}")
    else:
        _fields(c, declared.answer, result, method, "", found)
    _note(method, found)


def check_error(method: str, error: Any) -> None:
    """Check an error reply's failure, whether or not `method` exists."""
    c = contract()
    found: list[str] = []
    _value(c, Type(("failure",)), error, method, "error", found)
    _note(method, found)


def problems() -> list[str]:
    """Every problem found since the last `clear`, each once."""
    with _lock:
        return list(_kept)


def clear() -> None:
    with _lock:
        _kept.clear()
        _known.clear()
        _logged.clear()
