"""Spec 003: the pin tool and the download tool (AC7, and Ctrl+C as a Stop), against the test hosts.
The tools run in this process with the test CA; scripts/tool.js, which starts them for npm, has its
own test (tests/app/tool.test.js)."""

from __future__ import annotations

import _thread
import contextlib
import io
import json
import pickle
import signal
import struct
import threading
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import Holder, MakeNode, held_elsewhere
from fixtures.hosts import TRANSFER, Hosts, Stall, retry_after
from fixtures.store import sha, url_file

from oneframe import files
from oneframe.layout import Layout
from oneframe.models import tools
from oneframe.models.jobs import store_lock
from oneframe.models.pin import pin, sniff
from oneframe.models.store import Store, StoreRefused
from oneframe.models.transport import Policy


def torch_zip() -> bytes:
    """What torch.save writes: a zip archive holding <name>/data.pkl."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("model/data.pkl", pickle.dumps({"weights": [1.0, 2.0]}, protocol=2))
        archive.writestr("model/data/0", b"\x00" * 64)
    return buffer.getvalue()


def safetensors() -> bytes:
    header = json.dumps({"w": {"dtype": "F32", "shape": [2], "data_offsets": [0, 8]}}).encode()
    return struct.pack("<Q", len(header)) + header + b"\x00" * 8


MODEL = torch_zip()
SAFE = safetensors()
CONFIG = b'{"hidden": 1024}\n'
DEPTH = pickle.dumps({"state_dict": {"w": [0.5] * 100}}, protocol=4)


def write_node(make_node: MakeNode, files: list[dict[str, Any]], node_id: str = "test.moge") -> Path:
    folder = make_node(
        {
            "id": node_id,
            "version": "1",
            "title": "Pinned by the tool",
            "category": "test",
            "outputs": {"text": "Text"},
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
            "files": files,
        }
    )
    return folder / "node.json"


def files_of(path: Path) -> dict[str, dict[str, Any]]:
    return {row["name"]: row for row in json.loads(path.read_text(encoding="utf-8"))["files"]}


def run_pin(tmp_path: Path, node_root: Path, node: str = "test.moge") -> int:
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    return tools.pin_tool(data, [node_root], node, TRANSFER)


def test_pinning_fills_sizes_sha256s_formats_gated_and_the_canonical_id(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = hub.repo("Ruicheng/MoGe-2")
    commit = repo.commit({"model.bin": MODEL, "config.json": CONFIG, "model.safetensors": SAFE})
    gated = hub.repo("Org/Gated", gated="manual")
    gated.commit({"weights.pt": DEPTH})
    url = hub.add_file("/models/depth_pro.pt", DEPTH, redirect=True)
    path = write_node(
        make_node,
        [
            {"name": "model", "hf": "ruicheng/moge-2", "path": "model.bin"},  # a branch: main
            {"name": "config", "hf": "ruicheng/moge-2", "revision": "main", "path": "config.json"},
            {"name": "safe", "hf": "ruicheng/moge-2", "path": "model.safetensors"},
            {"name": "gated", "hf": "Org/Gated", "path": "weights.pt"},
            {"name": "apple", "url": url},
        ],
    )
    assert run_pin(tmp_path, node_root) == 1  # the gated file could not be fetched
    rows = files_of(path)
    for name, data, kind in (
        ("model", MODEL, "pickle"),
        ("config", CONFIG, "other"),
        ("safe", SAFE, "safetensors"),
    ):
        row = rows[name]
        assert (row["hf"], row["revision"], row["gated"]) == ("Ruicheng/MoGe-2", commit, False)
        assert (row["size"], row["sha256"], row["format"]) == (len(data), sha(data), kind)
    assert (rows["apple"]["sha256"], rows["apple"]["format"]) == (sha(DEPTH), "pickle")
    assert rows["gated"]["gated"] == f"{hub.hub}/Org/Gated" and "sha256" not in rows["gated"]
    report = json.loads(capsys.readouterr().out)
    assert [(f["name"], f["reason"]) for f in report["failed"]] == [("gated", "gated")]
    store = Store(tmp_path / "data")
    assert {sha(MODEL), sha(CONFIG), sha(SAFE), sha(DEPTH)} <= set(store.stored())
    assert store.partials() == []  # every partial placed
    # Pinned and present: a second pin at the same commit fetches nothing from the hub again.
    served = len(hub.served(MODEL))
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["files"] = [row for row in manifest["files"] if row["name"] != "gated"]
    path.write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    assert run_pin(tmp_path, node_root) == 0
    assert len(hub.served(MODEL)) == served


def test_a_re_pin_reports_each_sha256_that_changed(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = hub.repo("Ruicheng/MoGe-2")
    repo.commit({"model.bin": MODEL, "config.json": CONFIG})
    path = write_node(
        make_node,
        [
            {"name": "model", "hf": repo.id, "path": "model.bin"},
            {"name": "config", "hf": repo.id, "path": "config.json"},
        ],
    )
    assert run_pin(tmp_path, node_root) == 0
    capsys.readouterr()
    newer_model = torch_zip()[:-1] + b"\x01"
    second = repo.commit({"model.bin": newer_model, "config.json": CONFIG})
    manifest = json.loads(path.read_text(encoding="utf-8"))
    for row in manifest["files"]:
        row["revision"] = "main"  # re-pin to the newest commit
    path.write_text(json.dumps(manifest), encoding="utf-8")
    assert run_pin(tmp_path, node_root) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["changed"] == [{"name": "model", "old": sha(MODEL), "new": sha(newer_model)}]
    rows = files_of(path)
    assert {row["revision"] for row in rows.values()} == {second}
    assert rows["model"]["sha256"] == sha(newer_model) and rows["config"]["sha256"] == sha(CONFIG)


def test_a_changed_value_under_the_same_pin_is_reported_and_left(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    url = hub.add_file("/models/depth_pro.pt", DEPTH)
    path = write_node(make_node, [{"name": "apple", "url": url}])
    assert run_pin(tmp_path, node_root) == 0
    pinned = files_of(path)["apple"]
    capsys.readouterr()
    hub.add_file("/models/depth_pro.pt", DEPTH + b"replaced upstream")
    assert run_pin(tmp_path, node_root) == 0
    report = json.loads(capsys.readouterr().out)
    assert [(d["name"], d["recorded"], d["found"]) for d in report["drifted"]] == [
        ("apple", sha(DEPTH), sha(DEPTH + b"replaced upstream"))
    ]
    assert files_of(path)["apple"] == pinned


class _Killed(BaseException):
    """The write ended where it stands, by Ctrl+C or an error. (A process killed outright can also
    leave the temporary, which nothing reads: test_a_leftover_temporary_does_not_change_the_key.)"""


def test_a_pin_ended_while_writing_leaves_the_manifest_whole(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The manifest is written as the pin ends, a Stop included; the process ending during that
    write must leave node.json as it was, never truncated."""
    hub.repo("Ruicheng/MoGe-2").commit({"model.bin": MODEL})
    path = write_node(make_node, [{"name": "model", "hf": "ruicheng/moge-2", "path": "model.bin"}])
    before = path.read_text(encoding="utf-8")
    replace = files.replace

    def ended(source: Path, target: Path) -> None:
        if target.name == "node.json":  # the new manifest is written, and the process ends before it lands
            raise _Killed
        replace(source, target)

    monkeypatch.setattr(files, "replace", ended)
    with pytest.raises(_Killed):
        pin(path, Store(tmp_path / "data"), hub.hub, threading.Event(), lambda _e: None, TRANSFER)
    assert path.read_text(encoding="utf-8") == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["node.json", "node.py"]


def test_sniffing_reads_the_format_from_the_content(tmp_path: Path) -> None:
    cases = {"weights.bin": (MODEL, "pickle"), "a.pt": (DEPTH, "pickle"), "b.data": (SAFE, "safetensors")}
    cases["c.safetensors"] = (b"not really" * 3, "other")
    cases["d.zip"] = (b"PK\x03\x04 not a whole archive", "other")
    for name, (data, kind) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert sniff(tmp_path / name) == kind, name


def test_a_newer_store_refuses_pin(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_node(make_node, [{"name": "apple", "url": hub.add_file("/a.pt", DEPTH)}])
    store_file = Layout(tmp_path / "data").store_file
    store_file.parent.mkdir(parents=True)
    store_file.write_text('{"format": 5}', encoding="utf-8")
    said = _said_by_tools(monkeypatch)
    assert run_pin(tmp_path, node_root) == 2
    assert store_file.read_text(encoding="utf-8") == '{"format": 5}' and hub.requests() == []
    assert not _waiting(said)  # refused for what it is, at once: only `locked` is waited out


def _when(condition: Callable[[], bool], then: Callable[[], object]) -> threading.Thread:
    def watch() -> None:
        for _ in range(500):
            if condition():
                then()
                return
            threading.Event().wait(0.02)

    thread = threading.Thread(target=watch, daemon=True)
    thread.start()
    return thread


def _press_ctrl_c_when(condition: Callable[[], bool]) -> threading.Thread:
    return _when(condition, _thread.interrupt_main)  # what Ctrl+C does: KeyboardInterrupt in main


def test_ctrl_c_stops_the_download_tool_as_models_stop_does(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path
) -> None:
    url = hub.add_file("/models/depth_pro.pt", DEPTH * 50)
    hub.fault(url, Stall(at=10_000))
    data = DEPTH * 50
    write_node(
        make_node,
        [url_file("apple", data, format="pickle", url=url)],
    )
    root = tmp_path / "data"
    partial = Store(root).partial(sha(data))
    _press_ctrl_c_when(lambda: partial.exists() and partial.stat().st_size >= 10_000)
    slow = {**TRANSFER, "policy": Policy(stall_s=60)}
    assert tools.download(root, [node_root], "test.moge", None, slow) == tools.STOPPED
    assert partial.stat().st_size == 10_000
    journal = sorted(Layout(root).journals.glob("*model-download*"))[-1]
    assert json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])["event"] == "model.stopped"
    assert tools.download(root, [node_root], "test.moge", None, TRANSFER) == 0  # resumes and finishes
    assert Store(root).presence(sha(data), len(data)).status == "present"


def test_ctrl_c_stops_the_pin_tool_keeping_what_it_pinned(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path
) -> None:
    first = hub.add_file("/models/first.pt", DEPTH)
    url = hub.add_file("/models/depth_pro.pt", DEPTH * 50)
    hub.fault(url, Stall(at=10_000))
    path = write_node(make_node, [{"name": "first", "url": first}, {"name": "apple", "url": url}])
    store = Store(tmp_path / "data")
    partial = store.url_partial(url)
    _press_ctrl_c_when(lambda: partial.exists() and partial.stat().st_size >= 10_000)
    data = tmp_path / "data"
    data.mkdir()
    slow = {**TRANSFER, "policy": Policy(stall_s=60)}
    assert tools.pin_tool(data, [node_root], "test.moge", slow) == tools.STOPPED
    rows = files_of(path)
    assert rows["first"]["sha256"] == sha(DEPTH) and "sha256" not in rows["apple"]  # what it pinned is kept
    journal = sorted(Layout(data).journals.glob("*model-pin*"))[-1]
    assert json.loads(journal.read_text(encoding="utf-8").splitlines()[-1])["event"] == "pin.stopped"
    # The next pin starts the URL's file again: a partial keyed by a URL cannot tell old bytes from new.
    assert run_pin(tmp_path, node_root) == 0
    assert files_of(path)["apple"]["sha256"] == sha(DEPTH * 50)
    assert hub.requests("files")[-1].headers.get("range") is None


def test_a_hub_that_asks_for_a_long_wait_ends_the_pin_as_rate_limited(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = hub.repo("Ruicheng/MoGe-2")
    repo.commit({"model.bin": MODEL})
    write_node(make_node, [{"name": "model", "hf": repo.id, "path": "model.bin"}])
    hub.fault(f"{hub.hub}/api/models/{repo.id}/revision/main", retry_after(3600))
    assert run_pin(tmp_path, node_root) == 1
    assert "asked to wait 3600 s" in capsys.readouterr().err


# Another process holding the store: on Windows, npm gives the prompt back before a tool it ran has
# finished stopping (AC8's report, finding 3), so the next tool can meet the store still held.
def _store_held(data: Path) -> contextlib.AbstractContextManager[Holder]:
    """The store's lock, held by another process until it lets go (conftest's held_elsewhere)."""
    return held_elsewhere(store_lock(Store(data)).path)


def _said_by_tools(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    said: list[str] = []
    monkeypatch.setattr(tools, "_print", said.append)
    return said


def _waiting(said: list[str]) -> bool:
    return any("waiting up to" in line for line in said)


def _pinned(data: bytes) -> dict[str, Any]:
    return {"size": len(data), "sha256": sha(data), "format": "pickle"}


@pytest.mark.parametrize("tool", ["download", "pin"])
def test_a_tool_waits_for_a_store_another_process_still_holds_then_runs(
    tmp_path: Path,
    hub: Hosts,
    make_node: MakeNode,
    node_root: Path,
    monkeypatch: pytest.MonkeyPatch,
    tool: str,
) -> None:
    url = hub.add_file("/models/depth_pro.pt", DEPTH)
    write_node(make_node, [{"name": "apple", "url": url, **(_pinned(DEPTH) if tool == "download" else {})}])
    data = tmp_path / "data"
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_WAIT_S", 60.0)  # the holder lets go long before
    with _store_held(data) as holder:
        let_go: list[float] = []

        def let_go_now() -> None:
            let_go.append(time.monotonic())
            holder.let_go()

        letting_go = _when(lambda: _waiting(said), let_go_now)  # only once the tool says it waits
        if tool == "download":
            code = tools.download(data, [node_root], "test.moge", None, TRANSFER)
        else:
            code = tools.pin_tool(data, [node_root], "test.moge", TRANSFER)
        assert time.monotonic() - let_go[0] < 5  # bound: from the let-go; the tool would wait 60 s
        letting_go.join(10)
    assert code == 0, said
    assert _waiting(said[:1]), said  # it said so before anything else
    assert Store(data).presence(sha(DEPTH), len(DEPTH)).status == "present"


def test_ctrl_c_while_a_tool_waits_for_the_store_ends_it_with_nothing_started(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = hub.add_file("/models/depth_pro.pt", DEPTH)
    write_node(make_node, [{"name": "apple", "url": url, **_pinned(DEPTH)}])
    data = tmp_path / "data"
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_WAIT_S", 60.0)
    with _store_held(data):
        _press_ctrl_c_when(lambda: _waiting(said))
        assert tools.download(data, [node_root], "test.moge", None, TRANSFER) == tools.STOPPED
    assert said[-1] == "Stopped while waiting; nothing was started."
    assert hub.requests() == [] and not list(Layout(data).journals.glob("*model-download*"))


def test_a_store_held_past_the_wait_is_refused_locked_as_before(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_node(make_node, [{"name": "apple", "url": hub.add_file("/models/depth_pro.pt", DEPTH)}])
    data = tmp_path / "data"
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_WAIT_S", 0.3)
    with _store_held(data):
        assert tools.pin_tool(data, [node_root], "test.moge", TRANSFER) == 2
    assert _waiting(said) and said[-1].startswith("Another process on this data root is downloading")
    assert hub.requests() == []


def test_a_mistake_in_the_request_is_said_at_once_even_while_the_store_is_held(
    tmp_path: Path, hub: Hosts, make_node: MakeNode, node_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = hub.add_file("/models/depth_pro.pt", DEPTH)
    write_node(make_node, [{"name": "apple", "url": url, **_pinned(DEPTH)}])
    data = tmp_path / "data"
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_WAIT_S", 60.0)
    with _store_held(data):
        assert tools.download(data, [node_root], "test.moge", ["nope"], TRANSFER) == 2
    assert not _waiting(said) and "no checkpoint 'nope'" in said[-1]


def _scripted(attempts: list[int], refused: int, press_on: int | None = None) -> Callable[[], str]:
    """A claim refused `locked` `refused` times and then made; on attempt `press_on`, Ctrl+C."""

    def claim() -> str:
        attempts.append(len(attempts) + 1)
        if len(attempts) == press_on:
            signal.raise_signal(signal.SIGINT)  # what Ctrl+C does, at that very moment
        if len(attempts) <= refused:
            raise StoreRefused("Another process on this data root is downloading.", "locked")
        return "claimed"

    return claim


def test_a_tool_claims_again_until_the_store_is_let_go_saying_so_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_POLL_S", 0.01)
    attempts: list[int] = []
    before = signal.getsignal(signal.SIGINT)
    assert tools._claimed(_scripted(attempts, refused=3)) == ("claimed", False)
    assert attempts == [1, 2, 3, 4] and sum(_waiting([line]) for line in said) == 1
    assert signal.getsignal(signal.SIGINT) is before  # Ctrl+C is the tool's own again


def test_ctrl_c_during_a_refused_claim_ends_the_wait_and_during_a_made_one_is_passed_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    said = _said_by_tools(monkeypatch)
    monkeypatch.setattr(tools, "LOCK_POLL_S", 0.01)
    attempts: list[int] = []
    assert tools._claimed(_scripted(attempts, refused=5, press_on=2)) is None  # held back, not raised
    assert attempts == [1, 2] and said[-1] == "Stopped while waiting; nothing was started."
    attempts.clear()
    assert tools._claimed(_scripted(attempts, refused=1, press_on=2)) == ("claimed", True)
    # ... which the tool then treats as a Stop of what it started, at once
    started = threading.Event()
    thread = threading.Thread(target=started.wait, daemon=True)
    thread.start()
    assert tools._wait(thread, started.set, pressed=True) and not thread.is_alive()
