"""Spec 003: what the store holds, listed without the network, and removed safely (AC6, and
requirement 7's list). Each refusal says its reason; nothing outside the store is touched."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Events, MakeNode, call, held_elsewhere, wait_free
from fixtures.store import put, sha, url_file

from oneframe.files import lock_file
from oneframe.layout import Layout
from oneframe.models.jobs import Job
from oneframe.models.runs import READING, reading
from oneframe.models.store import Store
from oneframe.server import Engine

SHARED = b"a config both nodes pin\n" * 40
SMALL = b"small " * 3000
LARGE = b"large " * 7000
OWN = b"the other node's own weights " * 50
STRAY = b"a file from a node since deleted " * 30


@pytest.fixture
def nodes(make_node: MakeNode) -> None:
    base: dict[str, Any] = {
        "version": "1",
        "title": "Reads files",
        "category": "test",
        "outputs": {"text": "Text"},
        "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
    }
    make_node(
        {
            **base,
            "id": "test.moge",
            "params": {"checkpoint": {"type": "choice", "choices": ["small", "large"], "default": "small"}},
            "files_by": "checkpoint",
            "files": [
                url_file("config", SHARED, format="other"),
                url_file("small", SMALL, format="pickle", checkpoints=["small"]),
                url_file(
                    "large",
                    LARGE,
                    format="pickle",
                    checkpoints=["large"],
                    gated="https://huggingface.co/x/large",
                ),
            ],
            "licence": {"weights": "CC BY-NC 4.0"},
        }
    )
    make_node(
        {
            **base,
            "id": "test.other",
            "files": [url_file("config", SHARED, format="other"), url_file("own", OWN, format="pickle")],
        }
    )


@pytest.fixture
def engine(tmp_path: Path, node_root: Path, events: Events, nodes: None) -> Iterator[Engine]:
    found = Engine(tmp_path / "data", [node_root], events.append)
    try:
        yield found
    finally:
        found.shutdown(10)


def outside_the_store(data: Path) -> dict[str, bytes]:
    models = Layout(data).models
    return {
        str(p.relative_to(data)): p.read_bytes()
        for p in sorted(data.rglob("*"))
        if p.is_file() and models not in p.parents and "locks" not in p.parts
    }


def test_the_list_gives_each_group_its_sizes_status_and_what_removing_frees(engine: Engine) -> None:
    data = engine.data
    put(data, SHARED, SMALL, STRAY)
    store = Store(data)
    store.partial(sha(LARGE)).write_bytes(LARGE[:1000])
    store.url_partial("https://files.example/pinning.pt").write_bytes(b"x" * 10)
    listed = call(engine, "models.list")["result"]
    assert listed["store"] == "ok" and listed["free_bytes"] > 0
    moge = next(n for n in listed["nodes"] if n["node"] == "test.moge")
    assert moge["licence"] == {"weights": "CC BY-NC 4.0"}
    shared, small, large = moge["groups"]
    assert (shared["checkpoint"], shared["status"], shared["frees"]) == (
        None,
        "present",
        0,
    )  # test.other pins it
    assert shared["files"][0]["pinned_by"] == ["test.other"]
    assert (small["checkpoint"], small["default"], small["status"]) == ("small", True, "present")
    assert (small["size"], small["present"], small["to_fetch"], small["frees"]) == (
        len(SMALL),
        len(SMALL),
        0,
        len(SMALL),
    )
    row = large["files"][0]
    assert (row["status"], row["partial"], row["to_fetch"], row["format"]) == (
        "incomplete",
        1000,
        len(LARGE) - 1000,
        "pickle",
    )
    assert row["gated"] == "https://huggingface.co/x/large" and large["frees"] == 1000
    other = next(n for n in listed["nodes"] if n["node"] == "test.other")
    assert [g["status"] for g in other["groups"]] == ["partly"]
    assert listed["unpinned"]["files"] == [{"sha256": sha(STRAY), "size": len(STRAY)}]
    assert [p["name"] for p in listed["unpinned"]["partials"]] == [
        store.url_partial("https://files.example/pinning.pt").name
    ]


def test_removing_a_node_keeps_and_names_a_file_another_node_pins(engine: Engine) -> None:
    data = engine.data
    put(data, SHARED, SMALL, LARGE, OWN)
    before = outside_the_store(data)
    done = call(engine, "models.remove", node="test.other")["result"]
    assert done["kept"] == [{"name": "config", "sha256": sha(SHARED), "pinned_by": ["test.moge"]}]
    assert [(r["name"], r["freed"]) for r in done["removed"]] == [("own", len(OWN))]
    store = Store(data)
    assert set(store.stored()) == {sha(SHARED), sha(SMALL), sha(LARGE)}
    assert outside_the_store(data) == before


def test_removing_a_checkpoint_deletes_only_its_files_and_partials(engine: Engine) -> None:
    data = engine.data
    put(data, SHARED, SMALL, OWN)
    store = Store(data)
    store.partial(sha(LARGE)).write_bytes(LARGE[:500])
    done = call(engine, "models.remove", node="test.moge", checkpoint="large")["result"]
    assert [r["sha256"] for r in done["removed"]] == [sha(LARGE)] and done["freed"] == 500
    assert not store.partial(sha(LARGE)).exists()
    assert set(store.stored()) == {sha(SHARED), sha(SMALL), sha(OWN)}
    done = call(engine, "models.remove", node="test.moge", checkpoint="small")["result"]
    assert set(store.stored()) == {sha(SHARED), sha(OWN)} and done["kept"] == []
    assert (
        call(engine, "models.remove", node="test.moge", checkpoint="huge")["error"]["reason"] == "bad_params"
    )


def test_an_unpinned_list_deletes_exactly_that_list(engine: Engine) -> None:
    data = engine.data
    other_stray = STRAY + b"!"
    put(data, SHARED, STRAY, other_stray)
    store = Store(data)
    loose = store.url_partial("https://files.example/a.pt")
    loose.write_bytes(b"y")
    kept_loose = store.url_partial("https://files.example/b.pt")
    kept_loose.write_bytes(b"z")
    done = call(engine, "models.remove", unpinned=[sha(STRAY), loose.name])["result"]
    assert [r.get("sha256") or r.get("partial") for r in done["removed"]] == [sha(STRAY), loose.name]
    assert (
        set(store.stored()) == {sha(SHARED), sha(other_stray)} and kept_loose.exists() and not loose.exists()
    )
    refused = call(engine, "models.remove", unpinned=[sha(SHARED)])["error"]
    assert refused["reason"] == "bad_params" and "pinned by test.moge, test.other" in refused["message"]
    assert sha(SHARED) in store.stored()


def test_remove_is_refused_while_the_registry_has_problems_or_a_node_root_is_missing(
    engine: Engine, node_root: Path, tmp_path: Path, make_node: MakeNode
) -> None:
    put(engine.data, SHARED, STRAY, OWN)
    broken = node_root / "test.broken"
    broken.mkdir()
    (broken / "node.json").write_text("{not json", encoding="utf-8")
    call(engine, "nodes.reload")
    for params in ({"unpinned": [sha(STRAY)]}, {"node": "test.other"}):
        error = call(engine, "models.remove", **params)["error"]
        assert (error["kind"], error["reason"]) == ("store", "registry_problems")
    (broken / "node.json").unlink()
    broken.rmdir()
    call(engine, "nodes.reload")
    engine.node_roots.append(tmp_path / "unplugged")
    error = call(engine, "models.remove", unpinned=[sha(STRAY)])["error"]
    assert (error["kind"], error["reason"]) == ("store", "node_root_missing")
    assert set(Store(engine.data).stored()) == {sha(SHARED), sha(STRAY), sha(OWN)}


def test_remove_is_refused_during_a_job_in_this_process_or_another(engine: Engine, tmp_path: Path) -> None:
    put(engine.data, SHARED, OWN)
    with held_elsewhere(lock_file(Layout(engine.data).locks, "models")):
        error = call(engine, "models.remove", node="test.other")["error"]
        assert (error["kind"], error["reason"]) == ("store", "locked")
    engine.models._job = Job("abc", "download", "test.moge", [])  # a job of this engine's, running
    try:
        error = call(engine, "models.remove", node="test.other")["error"]
        assert (error["kind"], error["reason"]) == ("store", "busy")
    finally:
        engine.models._job = None
    assert set(Store(engine.data).stored()) == {sha(SHARED), sha(OWN)}


def test_remove_is_refused_for_a_file_a_running_graph_reads_in_this_process_or_another(
    engine: Engine, tmp_path: Path
) -> None:
    put(engine.data, SHARED, OWN)
    with reading(engine.scratch.folder, [sha(OWN)]):  # as this engine's scheduler lists a run's files
        error = call(engine, "models.remove", node="test.other")["error"]
    assert (error["kind"], error["reason"], error["files"]) == ("store", "in_use", [sha(OWN)])
    layout = Layout(engine.data)
    other = layout.tmp / "99"
    other.mkdir(parents=True)
    held = lock_file(layout.locks, "tmp-99")
    listed = json.dumps({"format": 1, "sha256": [sha(OWN)]})
    with held_elsewhere(held, write=(other / READING, listed)) as reader:
        error = call(engine, "models.remove", node="test.other")["error"]
        assert (error["reason"], error["files"]) == ("in_use", [sha(OWN)])
        reader.kill()  # it ends without taking its list away
    # once that process has ended and the system has let go of its lock, its list says nothing
    wait_free(held)
    done = call(engine, "models.remove", node="test.other")["result"]
    assert [r["sha256"] for r in done["removed"]] == [sha(OWN)]
    assert json.loads((other / READING).read_text(encoding="utf-8"))["sha256"] == [sha(OWN)]


def test_a_newer_store_refuses_remove_and_a_newer_record_is_left_as_it_is(engine: Engine) -> None:
    put(engine.data, SHARED, OWN)
    store = Store(engine.data)
    record = store.record_path(sha(OWN))
    newer = {"format": 7, "sha256": sha(OWN)}
    record.write_text(json.dumps(newer), encoding="utf-8")
    done = call(engine, "models.remove", node="test.other")["result"]
    assert done["removed"] == [] and done["failed"][0]["reason"] == "newer_format"
    assert json.loads(record.read_text(encoding="utf-8")) == newer and store.blob(sha(OWN)).exists()
    store.layout.store_file.write_text('{"format": 9}', encoding="utf-8")
    error = call(engine, "models.remove", unpinned=[])["error"]
    assert (error["kind"], error["reason"]) == ("store", "newer_format")
    assert store.layout.store_file.read_text(encoding="utf-8") == '{"format": 9}'
