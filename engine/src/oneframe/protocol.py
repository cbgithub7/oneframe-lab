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
from oneframe.records import (
    RECORD_KEYS,
    Record,
    Type,
    check_fields,
    check_value,
    kind_of,
    read_record,
)

LOGGER = logging.getLogger(__name__)

CONTRACT = CONTRACTS_DIR / "protocol.json"
FORMAT = 1
SENDERS = frozenset({"engine", "app"})
EVENT_KEYS = RECORD_KEYS | {"by"}
METHOD_KEYS = frozenset({"doc", "by", "queued", "null", "params", "answer"})
KEPT_MAX = 1000  # problems kept for problems(): an engine that runs for days stays bounded


class ProtocolError(ProblemsError):
    """The contract cannot be read, or holds problems."""


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
    shapes = {
        name: read_record(row, shapes_raw, f"shapes.{name}", problems) for name, row in shapes_raw.items()
    }
    events: dict[str, Record] = {}
    senders: dict[str, tuple[str, ...]] = {}
    for name, row in rows["events"].items():  # type: ignore[union-attr]
        events[name] = read_record(row, shapes_raw, f"events.{name}", problems, EVENT_KEYS)
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
            read_record(row.get("params"), shapes_raw, f"{where}.params", problems),
            read_record(row.get("answer"), shapes_raw, f"{where}.answer", problems),
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


def _contract() -> Contract | None:
    """The contract, or None, the problem kept, when it cannot be read: a check never stops a
    message, even then."""
    try:
        return contract()
    except ProtocolError as exc:
        _note("contracts/protocol.json", [str(exc)])
        return None


def check_event(message: dict[str, Any]) -> dict[str, Any]:
    """`message`, as it is, after checking it against its event's declaration."""
    name = message.get("event")
    c = _contract()
    if c is None:
        return message
    record = c.events.get(name) if isinstance(name, str) else None
    if record is None:
        _note(str(name), [f"{name!r} is not a declared event"])
        return message
    found: list[str] = []
    check_fields(c.shapes, record, message, str(name), "", found, beside="event")
    _note(str(name), found)
    return message


def event(name: str, /, **fields: Any) -> dict[str, Any]:
    """The event `name` with these fields, checked."""
    return check_event({"event": name, **fields})


def check_answer(method: str, result: Any, *, stopped: bool = False) -> None:
    """Check the result the engine answers `method` with; with `stopped`, that the method may be
    answered `{"stopped": true}` because it waits its turn. A method the engine does not have is
    answered stopped as rightly as it is refused (check_error), so only a declared one is held."""
    c = _contract()
    if c is None:
        return
    declared = c.methods.get(method)
    if declared is None and stopped:
        return
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
        found.append(f"{method}: the answer should be an object, not {kind_of(result)}")
    else:
        check_fields(c.shapes, declared.answer, result, method, "", found)
    _note(method, found)


def check_error(method: str, error: Any) -> None:
    """Check an error reply's failure, whether or not `method` exists."""
    c = _contract()
    if c is None:
        return
    found: list[str] = []
    check_value(c.shapes, Type(("failure",)), error, method, "error", found)
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
