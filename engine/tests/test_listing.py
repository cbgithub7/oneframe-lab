"""The test guard's collection hooks (spec 007, fixtures/listing.py), run on made-up suites."""

from __future__ import annotations

import pytest

PLUGIN = ("-p", "fixtures.listing")


def test_a_case_numbered_by_its_position_is_refused(
    pytester: pytest.Pytester, pytestconfig: pytest.Config
) -> None:
    assert pytestconfig.getini("strict_parametrization_ids") is True  # engine/pyproject.toml
    pytester.makeini("[pytest]\nstrict_parametrization_ids = true\n")
    pytester.makepyfile(
        test_by_position="""
            import pytest

            @pytest.mark.parametrize(("name", "settings"), [("a", {"x": 1}), ("b", {"x": 2})])
            def test_numbered(name, settings): ...
        """,
        test_twice="""
            import pytest

            @pytest.mark.parametrize("device", ["cpu", "cuda", "cpu"])
            def test_one_id_twice(device): ...
        """,
        test_named="""
            import enum, re
            import pytest

            class Kind(enum.Enum):
                METRIC = 1

            @pytest.mark.parametrize("settings", [{"x": 1}, {"x": 2}], ids=["one", "two"])
            def test_by_ids(settings): ...

            @pytest.mark.parametrize("settings", [pytest.param({"x": 1}, id="one")])
            def test_by_param(settings): ...

            VALUES = ["a", b"b", 1, 2.5, True, None, Kind.METRIC, re.compile("x"), len]

            @pytest.mark.parametrize("value", VALUES)
            def test_by_value(value): ...
        """,
    )

    numbered = pytester.runpytest(*PLUGIN, "--collect-only", "test_by_position.py")
    assert numbered.ret == pytest.ExitCode.INTERRUPTED
    numbered.stdout.fnmatch_lines(
        ["*test_by_position.py::test_numbered: a case would be numbered by its position (settings0, *"]
    )

    twice = pytester.runpytest(*PLUGIN, "--collect-only", "test_twice.py")
    assert twice.ret == pytest.ExitCode.INTERRUPTED
    twice.stdout.fnmatch_lines(
        ["*Duplicate parametrization IDs detected*", "*test_twice.py::test_one_id_twice*"]
    )

    named = pytester.runpytest(*PLUGIN, "--collect-only", "-q", "test_named.py")
    assert named.ret == pytest.ExitCode.OK
    assert sorted(line for line in named.outlines if "::" in line) == sorted(
        [
            "test_named.py::test_by_ids[one]",
            "test_named.py::test_by_ids[two]",
            "test_named.py::test_by_param[one]",
            *(
                f"test_named.py::test_by_value[{v}]"
                for v in ("a", "b", "1", "2.5", "True", "None", "Kind.METRIC", "x", "len")
            ),
        ]
    )
