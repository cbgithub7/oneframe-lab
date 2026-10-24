"""The test guard's collection hooks (spec 007, fixtures/listing.py), run on made-up suites."""

from __future__ import annotations

import textwrap

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


def test_the_listing_of_a_made_up_suite_is_as_expected(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(
        test_marks="""
            import pytest

            @pytest.mark.skip(reason="on the function")
            def test_skipped_on_the_function():
                assert True

            SKIPPED = pytest.param(2, marks=pytest.mark.skipif(True, reason="a case"))

            @pytest.mark.parametrize("n", [1, SKIPPED])
            def test_skipped_in_a_case(n):
                assert n

            @pytest.mark.xfail
            class TestOnTheClass:
                def test_skipped_on_the_class(self):
                    assert True
        """,
        test_module="""
            import sys
            import pytest

            pytestmark = pytest.mark.skipif(sys.platform == "nowhere", reason="on the module")

            def test_skipped_on_the_module():
                assert True

            def test_asserts():
                assert True
        """,
        test_calls="""
            import pytest

            def test_skip_call():
                pytest.skip("in the body")

            def test_xfail_call():
                if 1 > 2:
                    pytest.xfail("in a branch")
                assert True

            def test_importorskip_call():
                assert pytest.importorskip("json")

            def test_bare_xfail_call():
                from pytest import xfail

                xfail("imported by name")
        """,
        test_empty="""
            def test_pass():
                pass

            def test_ellipsis(): ...

            def test_docstring_only():
                \"\"\"Nothing here.\"\"\"

            def test_constants():
                \"\"\"Nothing but constants.\"\"\"
                "a string"
                0

            def test_return_first():
                return
                assert False

            def check():
                assert True

            def test_returns_a_check():
                return check()

            def test_asserts():
                \"\"\"The same name in a second file counts twice.\"\"\"
                assert 1 + 1 == 2
        """,
    )
    result = pytester.runpytest(*PLUGIN, "--collect-only", "--guard-listing")
    assert result.ret == pytest.ExitCode.OK
    listed = [line.removeprefix("listed\t") for line in result.outlines if line.startswith("listed\t")]
    assert "\n".join(listed) == textwrap.dedent("""\
        TestOnTheClass::test_skipped_on_the_class\t@xfail
        test_asserts
        test_asserts\t@skipif
        test_bare_xfail_call\txfail()
        test_constants\tempty
        test_docstring_only\tempty
        test_ellipsis\tempty
        test_importorskip_call\timportorskip()
        test_pass\tempty
        test_return_first\tempty
        test_returns_a_check
        test_skip_call\tskip()
        test_skipped_in_a_case[1]
        test_skipped_in_a_case[2]\t@skipif
        test_skipped_on_the_function\t@skip
        test_skipped_on_the_module\t@skipif
        test_xfail_call\txfail()""")
    unasked = pytester.runpytest(*PLUGIN, "--collect-only")
    assert unasked.ret == pytest.ExitCode.OK and not [line for line in unasked.outlines if "listed" in line]
