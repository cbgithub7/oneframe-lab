"""What a manifest may name besides ports: categories and param types (spec 019).

Read once from `contracts/vocabulary.json`, as `ports.types()` reads the port types, so the engine
keeps no list of its own and a category added there needs no engine edit. A test passes its own
copy by path. Read once a process, as the port types are: an edit is seen when the engine starts
again, not on `nodes.reload`. Standard library only, with `reading`, so a manifest is read
without loading more.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache
from pathlib import Path

from oneframe import CONTRACTS_DIR
from oneframe.reading import ProblemsError, read_object

CONTRACT = CONTRACTS_DIR / "vocabulary.json"
FORMAT = 1


class VocabularyError(ProblemsError):
    """The vocabulary contract cannot be read, or holds problems."""


@dataclass(frozen=True)
class Vocabulary:
    categories: tuple[str, ...]
    param_types: tuple[str, ...]


def _names(data: dict[str, object], key: str, problems: list[str]) -> tuple[str, ...]:
    rows = data.get(key)
    if not isinstance(rows, dict) or not rows:
        problems.append(f"{key} should be an object of names, each with its doc")
        return ()
    for name, row in rows.items():
        if not isinstance(row, dict) or not isinstance(row.get("doc"), str) or not row["doc"].strip():
            problems.append(f"{key}.{name} needs a doc")
    return tuple(rows)


@cache
def vocabulary(path: Path | None = None) -> Vocabulary:
    """The categories and param types, in the contract's order."""
    source = path or CONTRACT
    data, _ = read_object(source, VocabularyError)
    problems: list[str] = []
    if data.get("format") != FORMAT:
        problems.append(f"format {data.get('format')!r} is not {FORMAT}")
    found = Vocabulary(_names(data, "categories", problems), _names(data, "param_types", problems))
    if problems:
        raise VocabularyError(str(source), problems)
    return found
