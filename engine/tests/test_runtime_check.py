"""Spec 005: a runtime's code is checked at the Python that runtime runs, never the engine's (AC5)."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from conftest import MakeNode, MakeRuntime

from oneframe import runtime_check
from oneframe.registry import discover
from oneframe.runtimes.definition import discover as discover_runtimes
from oneframe.runtimes.manager import RuntimeMissing


def a_runtime(runtime_id: str, python: str) -> dict[str, Any]:
    return {
        "id": runtime_id,
        "title": runtime_id,
        "python": python,
        "builds": [{"name": "cpu", "vendor": "none"}],
    }


def a_node(node_id: str, runtime: str | None) -> dict[str, Any]:
    run = {"where": "runtime", "runtime": runtime, "entry": "node.py:run"} if runtime else None
    return {
        "id": node_id,
        "version": "1",
        "title": node_id,
        "category": "test",
        "outputs": {"text": "Text"},
        **({"run": run} if run else {}),
    }


def ruff_toml(folder: Path, target: str, extend: str = "../../ruff.toml") -> None:
    (folder / "ruff.toml").write_text(f'extend = "{extend}"\ntarget-version = "{target}"\n', encoding="utf-8")


def test_runtime_code_is_checked_at_its_runtimes_python(
    tmp_path: Path, make_runtime: MakeRuntime, runtime_root: Path, make_node: MakeNode, node_root: Path
) -> None:
    root_config = tmp_path / "ruff.toml"
    root_config.write_text('target-version = "py315"\n', encoding="utf-8")
    old = make_runtime(a_runtime("old", "3.11"))
    new = make_runtime(a_runtime("new", "3.14"))
    engine_node = make_node(a_node("test.engine", None))
    old_node = make_node(a_node("test.old", "old"))
    new_node = make_node(a_node("test.new", "new"))
    installed = {"old": tmp_path / "old" / "python"}

    def interpreter_for(runtime_id: str) -> Path:
        if runtime_id not in installed:
            raise RuntimeMissing(f"The runtime {runtime_id!r} is not installed.")
        return installed[runtime_id]

    found = runtime_check.groups(discover_runtimes([runtime_root]), discover([node_root]), interpreter_for)
    by_name = {g.name: g for g in found}
    assert set(by_name) == {"engine", "old", "new"}
    engine = by_name["engine"]
    assert engine.python == f"{sys.version_info.major}.{sys.version_info.minor}"
    assert engine.interpreter == Path(sys.executable) and engine.folders == (engine_node,)
    assert (by_name["old"].python, by_name["old"].interpreter) == ("3.11", installed["old"])
    assert by_name["old"].folders == (old, old_node)
    assert by_name["new"].python == "3.14" and by_name["new"].interpreter is None
    assert "not installed" in by_name["new"].why and by_name["new"].folders == (new, new_node)
    # pyright sees the node API alone of the engine, as a runtime child does.
    api = runtime_check.node_api(tmp_path / "scratch")
    config = runtime_check.pyright_config(by_name["old"], api)
    assert (config["pythonVersion"], config["extraPaths"]) == ("3.11", [str(api)])
    assert sorted(p.name for p in (api / "oneframe").iterdir()) == ["__init__.py", "node"]
    assert (api / "oneframe" / "node" / "context.py").is_file()
    # A node in a runtime there is no definition of would go unchecked, so it is named.
    orphan = make_node(a_node("test.orphan", "gone"))
    problems = runtime_check.unchecked(discover_runtimes([runtime_root]), discover([node_root]))
    assert problems == [f"{orphan}: runs in 'gone', which is no runtime here"]
    (orphan / "node.json").write_text("{", encoding="utf-8")  # and one whose manifest cannot be read
    problems = runtime_check.unchecked(discover_runtimes([runtime_root]), discover([node_root]))
    assert len(problems) == 1 and str(orphan) in problems[0], problems
    shutil.rmtree(orphan)

    # Each runtime folder and runtime node's folder holds a ruff.toml for its runtime's Python.
    ruff_toml(old, "py311")
    ruff_toml(old_node, "py311")
    ruff_toml(new, "py315")  # the engine's, not its runtime's
    ruff_toml(new_node, "py314", extend="ruff.toml")  # does not extend the repo's
    problems = runtime_check.ruff_problems(found, root_config)
    assert len(problems) == 2, problems
    assert str(new) in problems[0] and "targets 'py315'; its runtime runs 'py314'" in problems[0]
    assert str(new_node) in problems[1] and "does not extend" in problems[1]
    (old_node / "ruff.toml").unlink()
    assert any(
        str(old_node) in p and "no ruff.toml" in p for p in runtime_check.ruff_problems(found, root_config)
    )
    ruff_toml(old_node, "py311")
    ruff_toml(new, "py314")
    ruff_toml(new_node, "py314")
    assert runtime_check.ruff_problems(found, root_config) == []
