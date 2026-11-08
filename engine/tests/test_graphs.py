from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from oneframe import graphs
from oneframe.errors import Failure


def _graph(title: str, **extra: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "title": title,
        "nodes": {"a": {"node": "test.image", "params": {}}},
        "edges": [],
        **extra,
    }


def _write(folder: Path, name: str, data: Any) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return path


def test_graphs_are_found_by_looking(tmp_path: Path) -> None:
    shipped, saved = tmp_path / "shipped", tmp_path / "saved"
    _write(shipped, "first.json", _graph("First"))
    broken = _write(shipped, "broken.json", '{"version": 1, "nodes": ')
    _write(shipped, "notes.txt", "not a graph")
    (shipped / "folder.json").mkdir()
    _write(saved, "mine.json", _graph("Mine"))

    found = graphs.discover([shipped], saved)

    assert found.to_json()["graphs"] == [
        {"name": "first", "source": "shipped", "title": "First"},
        {"name": "mine", "source": "saved", "title": "Mine"},
    ]
    assert list(found.problems) == [str(broken)]
    assert "cannot read" in found.problems[str(broken)][0]
    assert found.get("mine").graph.title == "Mine"
    with pytest.raises(Failure, match="No graph called 'gone'"):
        found.get("gone")


def test_a_graph_file_that_cannot_be_a_graph_is_a_problem(tmp_path: Path) -> None:
    """A graph of the wrong shape, a name that is no id, and a name two shipped folders both use are
    each a problem of their own file. (A file Windows keeps the name of, `con.json`, cannot be made
    there; saving one is refused below.)"""
    one, two, saved = tmp_path / "one", tmp_path / "two", tmp_path / "saved"
    _write(one, "same.json", _graph("One"))
    twice = _write(two, "same.json", _graph("Two"))
    shape = _write(one, "shape.json", {"version": 1, "nodes": {"a": {"params": {}}}})
    upper = _write(one, "Upper.json", _graph("U"))

    found = graphs.discover([one, two], saved)

    assert [g["name"] for g in found.to_json()["graphs"]] == ["same"]
    assert found.get("same").graph.title == "One"
    assert set(found.problems) == {str(twice), str(shape), str(upper)}
    assert "already used" in found.problems[str(twice)][0]
    assert "a: a node should name its node" in found.problems[str(shape)][0]
    assert "lower case" in found.problems[str(upper)][0]
    assert graphs.name_problems("con") == ["'con' is a name Windows keeps for a device"]


def test_saving_keeps_shipped_graphs_and_newer_formats(tmp_path: Path) -> None:
    shipped, saved = tmp_path / "shipped", tmp_path / "saved"
    original = _write(shipped, "first.json", _graph("Shipped"))
    before = original.read_bytes()

    mine = _graph("Changed", page={"panes": [1, 2]})
    path = graphs.save(saved, "first", mine)
    assert path == saved / "first.json"
    assert json.loads(path.read_text(encoding="utf-8")) == mine
    assert original.read_bytes() == before
    listed = graphs.discover([shipped], saved)
    assert listed.to_json()["graphs"] == [
        {"name": "first", "source": "saved", "title": "Changed", "shadows": "shipped"}
    ]
    assert listed.get("first").graph.page == {"panes": [1, 2]}

    graphs.remove(saved, "first")
    assert graphs.discover([shipped], saved).to_json()["graphs"] == [
        {"name": "first", "source": "shipped", "title": "Shipped"}
    ]
    assert original.read_bytes() == before
    with pytest.raises(Failure, match="a shipped graph is never removed"):
        graphs.remove(saved, "first")

    newer = _write(saved, "newer.json", dict(_graph("From a newer app"), version=2))
    kept = newer.read_bytes()
    with pytest.raises(Failure) as refused:
        graphs.save(saved, "newer", _graph("Older"))
    assert (refused.value.kind, refused.value.reason) == ("graph", "newer_format")
    assert newer.read_bytes() == kept

    for bad in ("CON", "nul", "../out", "a/b", "", 3):
        with pytest.raises(Failure) as named:
            graphs.save(saved, bad, _graph("Bad"))
        assert (named.value.kind, named.value.reason) == ("request", "bad_params"), bad
    assert sorted(p.name for p in saved.iterdir()) == ["newer.json"]
