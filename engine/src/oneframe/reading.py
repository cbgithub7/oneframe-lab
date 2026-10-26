"""Reading a definition: a node's manifest, its memory model, a runtime's definition.

A definition is a JSON file in a folder of its own, found by looking. Each is read the same way:
every problem in it is collected and reported at once, so a person writing one fixes it in one
pass, and a broken one is kept as a problem beside the others, never an error that hides them.

Standard library only, so a module that reads a definition stays light.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Protocol, TypeIs

ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
VERSION = re.compile(r"^\d+(?:\.\d+)*$")


class ProblemsError(ValueError):
    """A definition that cannot be read, or holds problems: every one of them, and where."""

    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


def text(data: dict[str, Any], key: str, problems: list[str], required: bool = True) -> str:
    """`data[key]` as text; "" with a problem when it is not text or is blank, or "" when an
    optional one is missing."""
    value = data.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{key} should be a non-empty string")
        return ""
    return value


def optional_text(
    data: dict[str, Any], key: str, problems: list[str], where: str = "", default: str | None = None
) -> str | None:
    """`data[key]` as written when it is text, blank or not; `default` when it is missing or null;
    None, with a problem, when it is anything else. `where`: the object `data` is in the
    definition (`run`), so the problem names the field (`run.runtime`)."""
    value = data.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value
    problems.append(f"{f'{where}.' if where else ''}{key} should be text")
    return None


def check_id(value: str, problems: list[str]) -> str:
    """`value` if it is an id, lower case words joined by . _ or -; else "", with the problem
    added. An empty `value` is one `text` has already named."""
    if value and not ID.match(value):
        problems.append(f"id {value!r} should be lower case words joined by . _ or -")
        return ""
    return value


def number(value: Any) -> TypeIs[int | float]:
    """A JSON number: true and false are not."""
    return isinstance(value, int | float) and not isinstance(value, bool)


def version_tuple(text: str) -> tuple[int, ...]:
    """'560.94' -> (560, 94). A local label ('2.6.0+cu126') is dropped. Raises on anything else."""
    base = text.split("+", 1)[0].strip()
    if not VERSION.match(base):
        raise ValueError(f"{text!r} is not a version")
    return tuple(int(part) for part in base.split("."))


def at_least(value: str, floor: str) -> bool:
    """Whether version `value` is `floor` or newer; a missing part is 0, so "8" is "8.0"."""
    a, b = version_tuple(value), version_tuple(floor)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) >= b + (0,) * (width - len(b))


def at_most(value: str, ceiling: str) -> bool:
    """`ceiling` "12" covers every 12.x; "12.0" covers 12.0 only."""
    top = version_tuple(ceiling)
    return version_tuple(value)[: len(top)] <= top


def read_object(path: Path, error: type[ProblemsError]) -> tuple[dict[str, Any], bytes]:
    """The JSON object in `path`, and the bytes it was read from. A file that cannot be read,
    is not UTF-8, is not JSON or holds no object raises `error`, the caller's own kind."""
    try:
        read = path.read_bytes()
        data = json.loads(read.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise error(str(path), [f"cannot read: {exc}"]) from exc
    if not isinstance(data, dict):
        raise error(str(path), ["the file should hold one JSON object"])
    return data, read


class Definition(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def folder(self) -> Path: ...


def discover[T: Definition](
    roots: Iterable[Path], filename: str, load: Callable[[Path], T]
) -> tuple[dict[str, T], dict[str, list[str]]]:
    """Every definition at `<root>/*/<filename>`, by id, and the problems of those that are not
    usable, by path: one that raises a `ProblemsError`, or whose id an earlier one has."""
    found: dict[str, T] = {}
    problems: dict[str, list[str]] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{filename}")):
            try:
                item = load(path)
            except ProblemsError as exc:
                problems[str(path)] = exc.problems
                continue
            if item.id in found:
                problems[str(path)] = [f"id {item.id!r} is already used by {found[item.id].folder}"]
                continue
            found[item.id] = item
    return found, problems
