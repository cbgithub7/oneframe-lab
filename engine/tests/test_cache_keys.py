"""Results follow code (AC8 of spec 006): a node's cache key changes with any file in its folder,
and for a runtime node with its build and the hashes its runtime's marker records (lock,
definition, stand-ins); bytecode does not change it; the same code and runtime give the same key."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import AMPERE, NO_GPU, Events, MakeNode

from oneframe import runtime_install
from oneframe.cache import KEY_VERSION, Cache
from oneframe.graph import Graph
from oneframe.manifest import Manifest, parse
from oneframe.runtimes import Runtimes
from oneframe.scheduler import Scheduler

NODE = {
    "id": "test.keyed",
    "version": "1",
    "title": "Keyed",
    "category": "test",
    "outputs": {"text": "Text"},
    "params": {"n": {"type": "int", "default": 1}},
}


def _node(tmp_path: Path, runtime: str | None = None) -> Manifest:
    folder = tmp_path / "node"
    folder.mkdir(exist_ok=True)
    (folder / "node.py").write_text("def run(ctx):\n    pass\n", encoding="utf-8")
    (folder / "helper.py").write_text("X = 1\n", encoding="utf-8")
    row: dict[str, Any] = dict(NODE)
    if runtime:
        row["run"] = {"where": "runtime", "runtime": runtime, "entry": "node.py:run"}
    return parse(row, folder)


def test_the_key_version_is_2() -> None:
    assert KEY_VERSION == 2


def test_changing_any_file_in_the_nodes_folder_changes_its_key(tmp_path: Path) -> None:
    manifest = _node(tmp_path)
    cache = Cache(tmp_path / "cache")
    first = cache.key(manifest, {"n": 1}, {})
    (manifest.folder / "helper.py").write_text("X = 2\n", encoding="utf-8")
    second = cache.key(manifest, {"n": 1}, {})
    assert second != first
    (manifest.folder / "weights.json").write_text("{}", encoding="utf-8")  # a new file
    third = cache.key(manifest, {"n": 1}, {})
    assert third not in (first, second)
    (manifest.folder / "weights.json").unlink()
    assert cache.key(manifest, {"n": 1}, {}) == second


def test_bytecode_does_not_change_the_key(tmp_path: Path) -> None:
    manifest = _node(tmp_path)
    cache = Cache(tmp_path / "cache")
    before = cache.key(manifest, {"n": 1}, {})
    pycache = manifest.folder / "__pycache__"
    pycache.mkdir()
    (pycache / f"node.cpython-{sys.version_info[0]}{sys.version_info[1]}.pyc").write_bytes(b"\x00compiled")
    (manifest.folder / "stray.pyc").write_bytes(b"\x00")
    assert cache.key(manifest, {"n": 1}, {}) == before


def test_the_same_code_and_runtime_give_the_same_key(tmp_path: Path) -> None:
    manifest = _node(tmp_path, runtime="tiny")
    runtime = {"build": "cpu", "lock_sha256": "a", "definition_sha256": "b", "stand_ins_sha256": "c"}
    one = Cache(tmp_path / "one").key(manifest, {"n": 1}, {}, dict(runtime))
    two = Cache(tmp_path / "two").key(manifest, {"n": 1}, {}, dict(runtime))
    assert one == two


def test_a_runtimes_lock_definition_stand_ins_or_build_change_the_key(tmp_path: Path) -> None:
    manifest = _node(tmp_path, runtime="tiny")
    cache = Cache(tmp_path / "cache")
    base = {"build": "cpu", "lock_sha256": "a", "definition_sha256": "b", "stand_ins_sha256": "c"}
    keys = {cache.key(manifest, {"n": 1}, {}, base)}
    for name, value in (
        ("lock_sha256", "a2"),
        ("definition_sha256", "b2"),
        ("stand_ins_sha256", "c2"),
        ("build", "cu130"),
    ):
        keys.add(cache.key(manifest, {"n": 1}, {}, {**base, name: value}))
    assert len(keys) == 5


def _install_marker(data: Path, manager: Runtimes, build: str, **hashes: str) -> None:
    env = runtime_install.env_dir(data, "tiny", build)
    runtime_install.interpreter(env).parent.mkdir(parents=True)
    runtime_install.interpreter(env).write_text("", encoding="utf-8")
    row = {"format": 1, "runtime": "tiny", "build": build, "env": str(env.resolve())}
    row.update(manager.get("tiny").hashes())
    row.update(hashes)
    (env / runtime_install.MARKER).write_text(json.dumps(row), encoding="utf-8")


def test_the_runtime_part_comes_from_the_planned_build_and_its_marker(tmp_path: Path, tiny: Path) -> None:
    data = tmp_path / "data"
    cpu = Runtimes(data, [tiny.parent], uv=sys.executable, profile=lambda: NO_GPU)
    card = Runtimes(data, [tiny.parent], uv=sys.executable, profile=lambda: AMPERE)
    _install_marker(data, cpu, "cpu")
    _install_marker(data, cpu, "cu130")
    on_cpu, on_card = cpu.key_for("tiny"), card.key_for("tiny")
    assert (on_cpu["build"], on_card["build"]) == ("cpu", "cu130")
    assert {k: v for k, v in on_cpu.items() if k != "build"} == cpu.get("tiny").hashes()

    manifest = _node(tmp_path, runtime="tiny")
    cache = Cache(tmp_path / "cache")
    assert cache.key(manifest, {}, {}, on_cpu) != cache.key(manifest, {}, {}, on_card)

    # Reinstalled from another lock: the marker says so, and the key follows it.
    marker = runtime_install.env_dir(data, "tiny", "cpu") / runtime_install.MARKER
    row = json.loads(marker.read_text(encoding="utf-8"))
    marker.write_text(json.dumps({**row, "lock_sha256": "0" * 64}), encoding="utf-8")
    assert cpu.key_for("tiny")["lock_sha256"] == "0" * 64
    assert cache.key(manifest, {}, {}, cpu.key_for("tiny")) != cache.key(manifest, {}, {}, on_cpu)


def test_a_runtime_node_runs_again_when_its_runtime_changes(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler]
) -> None:
    make_node(
        {
            **NODE,
            "id": "test.in_runtime",
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
        },
        "def run(ctx):\n"
        "    p = ctx.path('t.txt')\n    p.write_text('x', encoding='utf-8')\n    ctx.output('text', p)\n",
    )
    built = {"build": "cpu", "lock_sha256": "a"}
    scheduler = scheduler_for(runtime_key=lambda _runtime: dict(built))
    graph = Graph.from_json({"version": 1, "nodes": {"n": {"node": "test.in_runtime"}}, "edges": []})

    def ran() -> bool:
        events = Events()
        assert scheduler.run(graph, events.append).status == "done"
        return bool(events.of("node.start"))

    assert ran() and not ran()  # the second comes from the cache
    built["lock_sha256"] = "b"  # the runtime was rebuilt from another lock
    assert ran()
