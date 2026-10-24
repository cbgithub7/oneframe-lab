"""Collection hooks for the test guard (spec 007), loaded by engine/tests/conftest.py and, in
test_listing.py, by the made-up suites pytester collects, so both run the same code.

A case whose ID pytest would number by its position (`memory0`, `exc3`) is refused, naming the
test: removing a middle case and adding one at the end would otherwise leave the IDs as they were.
Two cases of one ID are refused by pytest's own `strict_parametrization_ids`, set in
engine/pyproject.toml.

With `--guard-listing`, which only scripts/test-guard.js passes, collection also prints the listing
the guard compares: a line per test ID, prefixed `listed<TAB>`, sorted. An ID is the node ID without
its file, so a test can move between files; after a tab come its flags, if any, apart by spaces:

    @skip @skipif @xfail          a mark on its function, case, class or module
    skip() xfail() importorskip()  a call of pytest's in its body (`pytest.skip()`, or `skip()`)
    empty                         after the docstring, nothing but `pass`, `...` and constants,
                                  or a `return` without a value before anything else

Pytest escapes tabs and line ends in a case's ID, so neither can appear in one."""

from __future__ import annotations

import ast
import enum
import inspect
import re
import textwrap
from collections.abc import Callable, Generator, Sequence
from typing import Any

import pytest

PREFIX = "listed\t"
SKIP_MARKS = ("skip", "skipif", "xfail")
SKIP_CALLS = ("skip", "xfail", "importorskip")

_TEST = pytest.StashKey[str]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--guard-listing",
        action="store_true",
        help="print each test ID with its skips and empty flag, for scripts/test-guard.js",
    )


@pytest.hookimpl(wrapper=True)
def pytest_generate_tests(metafunc: pytest.Metafunc) -> Generator[None]:
    """Remember which test's cases are being named, for the refusal below."""
    metafunc.config.stash[_TEST] = metafunc.definition.nodeid
    try:
        return (yield)
    finally:
        del metafunc.config.stash[_TEST]


def pytest_make_parametrize_id(config: pytest.Config, val: object, argname: str) -> str | None:
    """Called for each value of a case that has no ID of its own: leave the values pytest names
    (as `_pytest.python.IdMaker._idval_from_value` does) and refuse the rest. A pytest that names
    other types shows: one more fails collection here, one fewer fails test_listing.py's IDs."""
    if (
        isinstance(val, str | bytes | float | int | bool | complex | re.Pattern | enum.Enum)
        or val is None
        or isinstance(getattr(val, "__name__", None), str)
    ):
        return None
    test = config.stash.get(_TEST, "a test")
    raise pytest.Collector.CollectError(
        f"{test}: a case would be numbered by its position ({argname}0, {argname}1, ...), "
        "so removing one and adding another would keep its ID. Name the cases with "
        "ids=[...] or pytest.param(..., id=...)."
    )


def pytest_report_collectionfinish(config: pytest.Config, items: Sequence[pytest.Item]) -> list[str]:
    if not config.getoption("guard_listing"):
        return []
    return [PREFIX + line for line in listing(items)]


def listing(items: Sequence[pytest.Item]) -> list[str]:
    lines: list[str] = []
    bodies: dict[Callable[..., Any], frozenset[str]] = {}  # a function's cases share its body
    for item in items:
        flags = {f"@{mark.name}" for mark in item.iter_markers() if mark.name in SKIP_MARKS}
        if isinstance(item, pytest.Function):
            if item.function not in bodies:
                bodies[item.function] = _body_flags(item.function)
            flags |= bodies[item.function]
        test = item.nodeid.split("::", 1)[1]
        lines.append(f"{test}\t{' '.join(sorted(flags))}" if flags else test)
    return sorted(lines)


def _body_flags(function: Callable[..., Any]) -> frozenset[str]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    definition = tree.body[0]
    assert isinstance(definition, ast.FunctionDef | ast.AsyncFunctionDef), function
    flags = {
        f"{name}()"
        for statement in definition.body
        for node in ast.walk(statement)
        if isinstance(node, ast.Call) and (name := _called(node.func)) in SKIP_CALLS
    }
    return frozenset(flags | {"empty"} if _empty(definition.body) else flags)


def _called(func: ast.expr) -> str | None:
    """`skip` for `pytest.skip(...)` or a bare `skip(...)`."""
    if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id == "pytest":
        return func.attr
    return func.id if isinstance(func, ast.Name) else None


def _empty(body: list[ast.stmt]) -> bool:
    for statement in body:
        if isinstance(statement, ast.Return):
            return statement.value is None or isinstance(statement.value, ast.Constant)
        if not (
            isinstance(statement, ast.Pass)
            or (isinstance(statement, ast.Expr) and isinstance(statement.value, ast.Constant))
        ):
            return False
    return True
