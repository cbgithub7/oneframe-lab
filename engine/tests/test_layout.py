"""The data root and its layout, against contracts/layout.json, the table the app's tests read too
(AC1 of spec 006)."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import pytest

from oneframe import CONTRACTS_DIR
from oneframe.layout import Layout, absolute, data_root, default_root

TABLE: dict[str, Any] = json.loads((CONTRACTS_DIR / "layout.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("row", TABLE["roots"], ids=lambda row: row["case"])
def test_every_row_gives_the_root_the_table_names(row: dict[str, Any]) -> None:
    got = data_root(row["env"], row["platform"], row["home"], row["packaged"])
    assert got == row["root"]
    # The answer is already in the platform's own spelling: its path flavour leaves it unchanged.
    flavour = PureWindowsPath if row["platform"] == "win32" else PurePosixPath
    assert str(flavour(got)) == got
    assert flavour(got).is_absolute()


def test_the_table_covers_every_variable_empty_missing_and_relative() -> None:
    variables = [
        ("linux", "XDG_DATA_HOME"),
        ("linux", "ONEFRAME_DATA"),
        ("win32", "LOCALAPPDATA"),
        ("win32", "ONEFRAME_DATA"),
    ]
    for platform, variable in variables:
        rows = [r for r in TABLE["roots"] if r["platform"] == platform]
        values = [r["env"].get(variable) for r in rows]
        assert None in values, (platform, variable, "missing")
        assert "" in values, (platform, variable, "empty")
        assert any(v and absolute(v, platform) is None for v in values), (platform, variable, "relative")
    assert {r["packaged"] for r in TABLE["roots"]} == {True, False}


def test_only_absolute_values_count_on_either_platform() -> None:
    assert absolute("C:x", "win32") is None
    assert absolute("\\x", "win32") is None
    assert absolute("//server", "win32") is None  # a share needs its name
    assert absolute("/x", "win32") is None
    assert absolute("C:\\", "win32") == "C:\\"
    assert absolute("x/y", "linux") is None
    assert absolute("/", "linux") == "/"
    assert absolute("/a/../../b", "linux") == "/b"  # never above the root


def test_a_home_that_is_not_absolute_is_refused_rather_than_guessed() -> None:
    with pytest.raises(ValueError, match="not an absolute path"):
        data_root({}, "linux", "ana", packaged=False)


def test_the_engine_default_is_the_dev_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("ONEFRAME_DATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert default_root().parent == tmp_path
    assert default_root().name in ("OneframeLab-dev", "oneframe-lab-dev")


def test_the_layout_names_the_paths_the_table_names() -> None:
    root = Path("root")
    assert Layout.names() == sorted(TABLE["paths"])
    for name, relative in TABLE["paths"].items():
        assert getattr(Layout(root), name) == root.joinpath(*relative.split("/")), name
