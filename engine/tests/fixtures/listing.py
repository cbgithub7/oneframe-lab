"""Collection hooks for the test guard (spec 007), loaded by engine/tests/conftest.py and, in
test_listing.py, by the made-up suites pytester collects, so both run the same code.

A case whose ID pytest would number by its position (`memory0`, `exc3`) is refused, naming the
test: removing a middle case and adding one at the end would otherwise leave the IDs as they were.
Two cases of one ID are refused by pytest's own `strict_parametrization_ids`, set in
engine/pyproject.toml."""

from __future__ import annotations

import enum
import re
from collections.abc import Generator

import pytest

_TEST = pytest.StashKey[str]()


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
    (as `_pytest.python.IdMaker._idval_from_value` does) and refuse the rest."""
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
