"""Spec 003: runs use only what is present (AC5). The files check comes after the fit and the
runtime check and before any process starts; a cached result needs no files; a node reads its
files through ctx.file; the fit never changes to a checkpoint that is not downloaded, and never to
one just because it is on disk."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import Events, HostRuntimes, MakeNode
from fixtures import fit_node, machines

from oneframe import bench_fit
from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.layout import Layout
from oneframe.models.runs import READING, in_use
from oneframe.models.store import Store
from oneframe.registry import discover
from oneframe.runtimes.manager import Runtimes
from oneframe.scheduler import Scheduler, for_root

SMALL = b"small weights " * 4000
LARGE = b"large weights " * 9000
CONFIG = b'{"layers": 4}\n'

# Reads its files through ctx.file and says what it read, and that it was listed as reading them.
READER = """
import hashlib, json, os
from pathlib import Path

def run(ctx):
    ctx.stage("pid", str(os.getpid()))
    seen = {}
    for name in ("config", "weights"):
        path = ctx.file(name)
        seen[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "path": str(path)}
    listed = Path(os.environ.get("READING_LIST", "")) if os.environ.get("READING_LIST") else None
    seen["listed"] = json.loads(listed.read_text())["sha256"] if listed and listed.is_file() else None
    try:
        ctx.file("other")
    except Exception as exc:
        seen["other"] = str(exc)
    out = ctx.path("read.json")
    out.write_text(json.dumps(seen), encoding="utf-8")
    ctx.output("text", out)
"""


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def put(data_root: Path, *contents: bytes) -> None:
    """Place files in the store as a finished download would."""
    store = Store(data_root)
    store.claim()
    for data in contents:
        partial = store.partial(sha(data))
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(data)
        store.place(partial, sha(data))


def pin(name: str, data: bytes, **more: Any) -> dict[str, Any]:
    url = f"https://files.example/{name}.pt"
    return {"name": name, "url": url, "size": len(data), "sha256": sha(data), "format": "other", **more}


def reader_node(make_node: MakeNode, memory: dict[str, Any] | None = None, default: str = "small") -> None:
    manifest: dict[str, Any] = {
        "id": "test.reader",
        "version": "1",
        "title": "Reads its files",
        "category": "test",
        "outputs": {"text": "Text"},
        "params": {"checkpoint": {"type": "choice", "choices": ["small", "large"], "default": default}},
        "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
        "devices": ["cpu"],
        "files_by": "checkpoint",
        "files": [
            pin("config", CONFIG),
            pin("weights", SMALL, checkpoints=["small"]),
            {**pin("weights-large", LARGE, checkpoints=["large"]), "name": "weights_large"},
        ],
    }
    if memory is not None:
        manifest["memory"] = memory
    make_node(manifest, READER)


def graph(**params: Any) -> Graph:
    return Graph.from_json(
        {"version": 1, "nodes": {"n": {"node": "test.reader", "params": params}}, "edges": []}
    )


def scheduler(data: Path, node_root: Path, machine: dict[str, Any] | None = None) -> Scheduler:
    return for_root(
        data,
        discover([node_root]),
        HostRuntimes(),
        Cache(data / "cache" / "objects", data / "cache" / "tmp"),
        machine=(lambda _cards: machine)
        if machine is not None
        else lambda _cards: machines.get("no GPU, 32 GB"),
        tmp_root=data / "cache" / "tmp" / "0",
    )


def failure(events: Events) -> dict[str, Any]:
    return events.of("node.failed")[0]


def test_before_any_download_the_node_fails_with_kind_files_and_no_process_starts(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events
) -> None:
    reader_node(make_node)
    result = scheduler(tmp_path / "data", node_root).run(graph(), events.append)
    assert result.status == "failed" and events.of("stage") == [] and events.of("node.start") == []
    failed = failure(events)
    assert (failed["kind"], failed["reason"], failed["retry"]) == ("files", "not_downloaded", False)
    assert failed["bytes_left"] == len(CONFIG) + len(SMALL)
    assert [(f["name"], f["state"]) for f in failed["files"]] == [
        ("config", "not_downloaded"),
        ("weights", "not_downloaded"),
    ]
    assert failed["next"] == "npm run models:download -- test.reader"
    assert "present_checkpoint" not in failed
    # a partial download, with no job running, is incomplete
    store = Store(tmp_path / "data")
    store.partial(sha(SMALL)).parent.mkdir(parents=True)
    store.partial(sha(SMALL)).write_bytes(SMALL[:1000])
    events.clear()
    scheduler(tmp_path / "data", node_root).run(graph(), events.append)
    assert failure(events)["reason"] == "incomplete"
    assert failure(events)["bytes_left"] == len(CONFIG) + len(SMALL) - 1000


def test_with_the_runtime_missing_too_the_failure_is_kind_runtime(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events
) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    found = for_root(
        data, discover([node_root]), None, Cache(data / "cache" / "objects", data / "cache" / "tmp")
    )  # no runtime installed
    found.run(graph(), events.append)
    assert failure(events)["kind"] == "runtime"


def test_after_the_download_the_node_reads_through_ctx_file_and_a_cached_result_needs_no_files(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events, monkeypatch: pytest.MonkeyPatch
) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL)
    listed = data / "cache" / "tmp" / "0" / READING
    monkeypatch.setenv("READING_LIST", str(listed))
    result = scheduler(data, node_root).run(graph(), events.append)
    assert result.status == "done", result.error
    seen = json.loads(result.outputs["n"]["text"].path.read_text(encoding="utf-8"))
    store = Store(data)
    assert seen["config"] == {"sha256": sha(CONFIG), "path": str(store.blob(sha(CONFIG)))}
    assert seen["weights"]["sha256"] == sha(SMALL)
    assert seen["listed"] == sorted([sha(CONFIG), sha(SMALL), sha(LARGE)])  # all it could read, while it ran
    assert "'other' is not a file this run reads (it reads: config, weights)" in seen["other"]
    assert not listed.exists()  # and no longer
    # The cached result is served even with the files gone.
    store.drop(sha(SMALL))
    events.clear()
    again = scheduler(data, node_root).run(graph(), events.append)
    assert again.status == "done" and events.kinds()[1] == "node.cached"


def test_a_changed_file_reads_as_changed(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events
) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL)
    blob = Store(data).blob(sha(SMALL))
    blob.chmod(0o644)
    info = blob.stat()
    os.utime(blob, ns=(info.st_atime_ns, info.st_mtime_ns + 5 * 10**9))  # a new modification time
    scheduler(data, node_root).run(graph(), events.append)
    failed = failure(events)
    assert (failed["reason"], failed["files"][0]["why"]) == (
        "changed",
        "it was modified after it was checked",
    )
    assert failed["next"].startswith("Download again (npm run models:download -- test.reader)")
    blob.write_bytes(SMALL[:-10])  # truncated
    events.clear()
    scheduler(data, node_root).run(graph(), events.append)
    assert failure(events)["reason"] == "changed"
    # a change that keeps both the size and the time is models.verify's to catch (test_model_jobs)


def test_a_result_is_stored_under_the_manifest_the_run_used(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events
) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL, LARGE)
    loaded = scheduler(data, node_root)  # the registry as loaded: the old pins
    first = loaded.run(graph(), events.append)
    key = first.records["n"]["key"]
    # A re-pin on disk, without a reload: the weights now name the large file.
    path = node_root / "test.reader" / "node.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"][1] = pin("weights", LARGE, checkpoints=["small"])
    path.write_text(json.dumps(manifest), encoding="utf-8")
    events.clear()
    second = loaded.run(graph(), events.append)
    assert second.records["n"] == {"cached": True, "key": key}  # it used the old pins, under their key
    events.clear()
    reloaded = scheduler(data, node_root).run(graph(), events.append)
    assert reloaded.records["n"]["key"] != key and not reloaded.records["n"]["cached"]
    seen = json.loads(reloaded.outputs["n"]["text"].path.read_text(encoding="utf-8"))
    assert seen["weights"]["sha256"] == sha(LARGE)


CHECKPOINT_MODEL: dict[str, Any] = {
    "precisions": {"fp32": {"cuda_min_capability": None, "cpu": True}},
    "weights_by": "checkpoint",
    "weights": {"small": {"fp32": 1000}, "large": {"fp32": 3000}, "source": fit_node.SOURCE},
    "working": {"mb": 100, "source": fit_node.SOURCE},
    "changes": [{"set": {"checkpoint": "small"}, "costs": "quality"}],
}
ROOMY = machines.machine([], 20000, 34360)
TIGHT = machines.machine([], 3500, 8000)  # margin 1.6 GB: small (1.1 GB) fits, large (3.1 GB) does not


@pytest.mark.parametrize(
    ("default", "params"),
    [("small", {"checkpoint": "large"}), ("large", {})],
    ids=["large set over a small default", "large by default"],
)
def test_the_engine_never_switches_to_a_checkpoint_because_it_is_on_disk(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events, default: str, params: dict[str, Any]
) -> None:
    reader_node(make_node, copy.deepcopy(CHECKPOINT_MODEL), default=default)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL)
    scheduler(data, node_root, machine=ROOMY).run(graph(**params), events.append)
    fit = events.of("node.fit")[0]
    assert (fit["outcome"], fit["values"]["checkpoint"]) == ("fits", "large")
    failed = failure(events)
    assert (failed["kind"], failed["reason"], failed["present_checkpoint"]) == (
        "files",
        "not_downloaded",
        "small",
    )
    assert "Its checkpoint small is downloaded." in failed["message"]
    assert failed["next"] == "npm run models:download -- test.reader" + (
        " --checkpoint large" if default == "small" else ""
    )


def test_a_change_to_a_checkpoint_that_is_not_downloaded_is_skipped_and_named_when_only_it_fits(
    tmp_path: Path, make_node: MakeNode, node_root: Path, events: Events
) -> None:
    reader_node(make_node, copy.deepcopy(CHECKPOINT_MODEL), default="large")
    data = tmp_path / "data"
    put(data, CONFIG, LARGE)
    scheduler(data, node_root, machine=TIGHT).run(graph(), events.append)
    fit = events.of("node.fit")[0]
    assert fit["skipped"] == [{"change": 0, "why": "checkpoint small is not downloaded"}]
    failed = failure(events)
    assert failed["kind"] == "memory"
    assert (
        "Only checkpoint small would fit in the free memory, and it is not downloaded "
        f"({round(len(SMALL) / 1e6)} MB to download)" in failed["message"]
    )
    # With small downloaded too, the fit changes to it for memory, and the node runs.
    put(data, SMALL)
    events.clear()
    result = scheduler(data, node_root, machine=TIGHT).run(graph(), events.append)
    assert result.status == "done", result.error
    assert events.of("node.fit")[0]["values"]["checkpoint"] == "small"


def test_bench_fit_reads_files_too(tmp_path: Path, make_node: MakeNode, node_root: Path, tiny: Path) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL)
    manager = Runtimes(data, [tiny.parent], uv=sys.executable, profile=lambda: machines.get("no GPU, 32 GB"))
    manager.python_for = lambda _r: Path(sys.executable)  # type: ignore[method-assign]
    found = bench_fit.make_scheduler(data, discover([node_root]), manager, tmp_path / "bench-cache")
    report = bench_fit.bench_node(found, "test.reader", [{}])
    assert report["runs"][0]["status"] == "done", report["runs"][0]


def test_in_use_reads_the_lists_of_live_processes_only(tmp_path: Path) -> None:
    from oneframe.files import FileLock, lock_file

    layout = Layout(tmp_path / "data")
    for slot in ("0", "1"):  # 0's process is alive (its lock held below); 1's has ended
        folder = layout.tmp / slot
        folder.mkdir(parents=True)
        (folder / READING).write_text(json.dumps({"format": 1, "sha256": [slot * 64]}), encoding="utf-8")
    lock = FileLock(lock_file(layout.locks, "tmp-0"))
    assert lock.acquire()
    try:
        assert in_use(layout) == {"0" * 64}
    finally:
        lock.release()
    assert in_use(layout) == set()


def test_in_the_tiny_runtime_a_node_reads_its_files_through_ctx_file(
    tmp_path: Path,
    make_node: MakeNode,
    node_root: Path,
    events: Events,
    tiny: Path,
    uv_exe: str,
    uv_home: Path,
) -> None:
    reader_node(make_node)
    data = tmp_path / "data"
    put(data, CONFIG, SMALL)
    manager = Runtimes(
        data, [tiny.parent], uv=uv_exe, profile=lambda: machines.get("no GPU, 32 GB"), uv_home=uv_home
    )
    assert manager.install("tiny") is not None
    found = for_root(
        data, discover([node_root]), manager, Cache(data / "cache" / "objects", data / "cache" / "tmp")
    )
    result = found.run(graph(), events.append)
    assert result.status == "done", result.error
    seen = json.loads(result.outputs["n"]["text"].path.read_text(encoding="utf-8"))
    assert (seen["config"]["sha256"], seen["weights"]["sha256"]) == (sha(CONFIG), sha(SMALL))
