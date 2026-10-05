"""One failure model (AC6 of spec 006): kinds and reasons are declared once, in errors.py; nothing
undeclared can be raised; the table in docs/architecture.md says the same, both ways; every failure
carries kind, reason, message, next and retry; and a facet mismatch found at run time is reported
against the edge, not blamed on the node that receives it."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import NO_GPU, Events, MakeNode

from oneframe import BUILTIN_NODES_DIR, REPO_ROOT, archives, child, errors, executors
from oneframe.errors import KINDS, Failure, UndeclaredFailure, check, failure
from oneframe.executors import NodeError
from oneframe.graph import Graph
from oneframe.runtimes import InstallRefused, RuntimeMissing
from oneframe.scheduler import Scheduler
from oneframe.server import Engine


def _table() -> dict[tuple[str, str | None], tuple[bool, str]]:
    """docs/architecture.md's failure table: (kind, reason) -> (retry, meaning)."""
    text = (REPO_ROOT / "docs" / "architecture.md").read_text(encoding="utf-8")
    rows: dict[tuple[str, str | None], tuple[bool, str]] = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 4 or cells[2] not in ("yes", "no") or not cells[0].startswith("`"):
            continue
        kind, reason = cells[0].strip("`"), cells[1].strip("`") or None
        assert (kind, reason) not in rows, f"{kind}/{reason} is in the table twice"
        rows[(kind, reason)] = (cells[2] == "yes", cells[3])
    return rows


def _declared() -> dict[tuple[str, str | None], tuple[bool, str]]:
    out: dict[tuple[str, str | None], tuple[bool, str]] = {}
    for name, kind in KINDS.items():
        out[(name, None)] = (kind.retry, kind.meaning)
        for reason, (retry, meaning) in kind.reasons.items():
            out[(name, reason)] = (retry, meaning)
    return out


def test_the_declaration_and_the_architecture_table_are_equal_both_ways() -> None:
    table, declared = _table(), _declared()
    assert sorted(set(declared) - set(table), key=str) == [], "declared, but not in the table"
    assert sorted(set(table) - set(declared), key=str) == [], "in the table, but not declared"
    assert table == declared


def test_every_reason_is_snake_case() -> None:
    for kind in KINDS.values():
        for reason in kind.reasons:
            assert re.fullmatch(r"[a-z]+(_[a-z]+)*", reason), reason


def test_an_undeclared_kind_or_reason_cannot_be_raised() -> None:
    with pytest.raises(UndeclaredFailure, match="'bogus' is not a declared kind"):
        NodeError("bogus", "a node failed")
    with pytest.raises(UndeclaredFailure, match="'not installed' is not a declared reason"):
        RuntimeMissing("missing", "not installed")  # the old spelling
    with pytest.raises(UndeclaredFailure):
        InstallRefused("refused", "too_tired")
    with pytest.raises(UndeclaredFailure):
        Failure("x", reason="busy", kind="oom")  # a real reason, of another kind
    with pytest.raises(UndeclaredFailure):
        failure("oom", "x", reason="busy")
    assert check("runtime", "locked").reasons["locked"][0] is True


def test_a_failure_carries_kind_reason_message_next_and_retry() -> None:
    error = RuntimeMissing(
        "The runtime 'tiny' is not installed.", "not_installed", "Install the runtime tiny."
    )
    assert error.to_json() == {
        "kind": "runtime",
        "reason": "not_installed",
        "message": "The runtime 'tiny' is not installed.",
        "next": "Install the runtime tiny.",
        "retry": False,
    }
    assert InstallRefused("busy", "locked").to_json()["retry"] is True  # a reason's retry wins
    assert NodeError("oom", "out of memory").to_json() == {
        "kind": "oom",
        "message": "out of memory",
        "retry": True,
    }


def test_there_is_one_stopped() -> None:
    assert errors.Stopped is child.Stopped is executors.Stopped is archives.Stopped


def test_error_replies_carry_the_failure(tmp_path: Path) -> None:
    engine = Engine(tmp_path, [BUILTIN_NODES_DIR], lambda _m: None, profile=lambda: NO_GPU)
    try:
        unknown = engine.handle({"id": 1, "method": "nodes.explode"}) or {}
        assert unknown["error"] == {
            "kind": "request",
            "reason": "unknown_method",
            "message": "Unknown method 'nodes.explode'.",
            "retry": False,
        }
        missing = engine.handle({"id": 2, "method": "runtimes.install", "params": {"runtime": "nope"}}) or {}
        assert (missing["error"]["kind"], missing["error"]["reason"]) == ("runtime", "unknown")
        graph = {"version": 1, "nodes": {"a": {"node": "no.such"}}, "edges": []}
        bad = engine.handle({"id": 3, "method": "graph.validate", "params": {"graph": graph}}) or {}
        assert bad["error"]["kind"] == "graph" and bad["error"]["problems"]
        missing = engine.handle({"id": 4, "method": "graph.validate", "params": {}}) or {}  # no graph
        assert (missing["error"]["kind"], missing["error"]["reason"]) == ("request", "bad_params")
        unknown = engine.handle({"id": 5, "method": "nodes.fit", "params": {"node": "no.such"}}) or {}
        assert unknown["error"]["reason"] == "bad_params" and "no.such" in unknown["error"]["message"]

        def broken(_params: dict[str, object]) -> dict[str, object]:
            raise KeyError("an engine bug")

        engine.methods["ports.list"] = broken
        crash = engine.handle({"id": 6, "method": "ports.list"}) or {}
        assert crash["error"]["kind"] == "engine" and "KeyError" in crash["error"]["message"]
        assert "Traceback" in crash["error"]["detail"]
    finally:
        engine.shutdown()


OPEN_MASK = {
    "id": "test.open_mask",
    "version": "1",
    "title": "A mask whose kind is known only when it runs",
    "category": "test",
    "outputs": {"mask": {"type": "Mask", "trust": "predicted"}},
}
NEEDS_BINARY = {
    "id": "test.needs_binary",
    "version": "1",
    "title": "Needs a binary mask",
    "category": "test",
    "inputs": {"mask": "Mask[kind=binary]"},
    "outputs": {"mask": {"type": "Mask[kind=binary]", "trust": "predicted"}},
}


def test_a_facet_mismatch_at_run_time_is_the_edges_failure_not_the_nodes(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    make_node(
        OPEN_MASK,
        "def run(ctx):\n"
        "    p = ctx.path('m.png')\n    p.write_bytes(b'x')\n"
        "    ctx.output('mask', p, facets={'kind': 'soft', 'marks': 'object'})\n",
    )
    make_node(NEEDS_BINARY, "def run(ctx):\n    raise AssertionError('never reached')\n")
    graph = Graph.from_json(
        {
            "version": 1,
            "nodes": {"m": {"node": "test.open_mask"}, "b": {"node": "test.needs_binary"}},
            "edges": [{"from": "m.mask", "to": "b.mask"}],
        }
    )
    result = scheduler_for().run(graph, events.append)
    assert result.status == "failed" and result.error is not None
    assert result.error["kind"] == "edge"
    assert result.error["edge"] == {"from": "m.mask", "to": "b.mask"}
    assert "binary" in result.error["message"]
    assert events.of("node.failed") == []  # no node is blamed
    [failed] = events.of("run.failed")
    assert failed["kind"] == "edge" and failed["edge"] == {"from": "m.mask", "to": "b.mask"}
    assert "step" not in failed and failed["retry"] is False


def test_a_child_that_names_an_undeclared_kind_still_fails_as_a_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code = tmp_path / "node.py"
    code.write_text("def run(ctx):\n    raise RuntimeError('boom')\n", encoding="utf-8")
    job = {
        "node": "test.x",
        "entry": {"file": str(code), "function": "run"},
        "out_dir": str(tmp_path / "out"),
    }
    monkeypatch.setattr(child, "classify", lambda _exc: "made_up")  # a kind nobody declared
    with pytest.raises(NodeError) as caught:
        executors.EngineExecutor().execute({**job, "device": "cpu"}, lambda _e: None, lambda: False)
    assert caught.value.kind == "error" and "boom" in str(caught.value)
    assert executors.declared_kind("made_up") == "error" and executors.declared_kind("oom") == "oom"


def test_an_engine_that_cannot_start_says_why_on_its_protocol(tmp_path: Path) -> None:
    """Any start failure, not only a refused root, reaches the app as engine.failed with a reason."""
    (tmp_path / "a-file").write_text("not a folder", encoding="utf-8")
    done = subprocess.run(
        [sys.executable, "-m", "oneframe.server", "--data", str(tmp_path / "a-file" / "root")],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 1
    event = json.loads(done.stdout.splitlines()[0])
    assert (event["event"], event["kind"], event["retry"]) == ("engine.failed", "engine", False)
    assert "Error" in event["message"] and "Traceback" in event["detail"]
