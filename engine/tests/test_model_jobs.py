"""Spec 003: download and verify are jobs (AC2, AC3, AC6's busy and locked), run by the engine as
the app runs it, against the test hosts over TLS."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import Events, MakeNode
from fixtures.hosts import Hosts, Replace, Stall, client_context

from oneframe.files import lock_file
from oneframe.layout import Layout
from oneframe.models.store import Store
from oneframe.models.transport import Policy
from oneframe.server import Engine

FAST = Policy(stall_s=0.5, attempts=3, first_wait_s=0.01, longest_wait_s=0.05, longest_asked_s=5)
SHARED = b"config " * 1000
SMALL = bytes(range(256)) * 2048  # 512 KiB
LARGE = bytes(reversed(range(256))) * 4096  # 1 MiB
OTHER = b"another node's own file" * 100


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def pinned(name: str, url: str, data: bytes, **more: Any) -> dict[str, Any]:
    return {"name": name, "url": url, "size": len(data), "sha256": sha(data), "format": "other", **more}


def hub_pinned(name: str, repo: str, commit: str, path: str, data: bytes, **more: Any) -> dict[str, Any]:
    row = {"name": name, "hf": repo, "revision": commit, "path": path, "size": len(data), "sha256": sha(data)}
    return {**row, "format": "pickle", **more}


def node_with_files(make_node: MakeNode, node_id: str, files: list[dict[str, Any]], **more: Any) -> None:
    make_node(
        {
            "id": node_id,
            "version": "1",
            "title": "Reads files",
            "category": "test",
            "outputs": {"text": "Text"},
            "params": {"checkpoint": {"type": "choice", "choices": ["small", "large"], "default": "small"}},
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
            "files_by": "checkpoint",
            "files": files,
            **more,
        }
    )


@pytest.fixture
def hub(hosts: Hosts, monkeypatch: pytest.MonkeyPatch) -> Hosts:
    monkeypatch.setenv("HF_ENDPOINT", hosts.hub)
    return hosts


@pytest.fixture
def pinned_node(hub: Hosts, make_node: MakeNode, engine: Engine) -> dict[str, str]:
    """test.files: a shared config on the hub, and a small and a large checkpoint on the files host,
    the large one behind a single-use redirect."""
    repo = hub.repo("Ruicheng/moge-3-vitl")
    commit = repo.commit({"config.json": SHARED})
    urls = {
        "config": hub.resolve_url(repo.id, commit, "config.json"),
        "small": hub.add_file("/models/small.pt", SMALL),
        "large": hub.add_file("/models/large.pt", LARGE, redirect=True),
    }
    node_with_files(
        make_node,
        "test.files",
        [
            {**hub_pinned("config", repo.id, commit, "config.json", SHARED), "format": "other"},
            pinned("small", urls["small"], SMALL, checkpoints=["small"]),
            pinned("large", urls["large"], LARGE, checkpoints=["large"]),
        ],
    )
    call(engine, "nodes.reload")
    return urls


@pytest.fixture
def engine(tmp_path: Path, node_root: Path, events: Events, hub: Hosts) -> Iterator[Engine]:
    found = Engine(
        tmp_path / "data",
        [node_root],
        events.append,
        transfer={"context": client_context, "policy": FAST, "proxy": lambda _host: None},
    )
    try:
        yield found
    finally:
        found.shutdown(10)


def call(engine: Engine, method: str, **params: Any) -> dict[str, Any]:
    return engine.handle({"id": 1, "method": method, "params": params}) or {}


def wait_end(events: Events, job: str, seconds: float = 30) -> dict[str, Any]:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for event in list(events):
            if event.get("job") == job and event["event"] in ("model.done", "model.failed", "model.stopped"):
                return event
        time.sleep(0.02)
    raise AssertionError(f"job {job} did not end: {events.kinds()}")


def run_job(engine: Engine, events: Events, method: str, **params: Any) -> dict[str, Any]:
    reply = call(engine, method, **params)
    assert "result" in reply, reply
    return wait_end(events, reply["result"]["job"])


def wait_until(check: Callable[[], bool], seconds: float = 10) -> None:
    deadline = time.monotonic() + seconds
    while not check():
        assert time.monotonic() < deadline
        time.sleep(0.02)


def test_a_download_is_a_job_that_fetches_each_file_once(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts, make_node: MakeNode
) -> None:
    reply = call(engine, "models.download", node="test.files")
    job = reply["result"]["job"]
    assert [f["name"] for f in reply["result"]["files"]] == ["config", "small"]  # shared, and the default
    end = wait_end(events, job)
    assert end["event"] == "model.done"
    assert [(f["name"], f["outcome"]) for f in end["files"]] == [
        ("config", "downloaded"),
        ("small", "downloaded"),
    ]
    mine = [e for e in events if e.get("job") == job]
    assert mine == [e for e in events if e["event"].startswith("model.")]  # every event carries the id
    assert mine[0]["event"] == "model.start"
    phases = [e["phase"] for e in mine if e["event"] == "model.file" and e["file"] == "small"]
    assert phases == ["downloading", "verifying", "placing"]
    store = Store(engine.data)
    assert store.presence(sha(SMALL), len(SMALL)).status == "present"
    # A second node pinning the same file, at the same commit or another URL, fetches nothing.
    node_with_files(make_node, "test.same", [pinned("again", pinned_node["small"] + "?v=2", SMALL)])
    call(engine, "nodes.reload")
    end = run_job(engine, events, "models.download", node="test.same")
    assert [f["outcome"] for f in end["files"]] == ["present"]
    end = run_job(engine, events, "models.download", node="test.files", checkpoints="all")
    assert [(f["name"], f["outcome"]) for f in end["files"]] == [
        ("config", "present"),
        ("small", "present"),
        ("large", "downloaded"),
    ]
    for data in (SHARED, SMALL, LARGE):
        assert len(hub.served(data)) == 1, data[:10]


def test_the_journal_names_the_hosts_each_file_was_fetched_from(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts
) -> None:
    run_job(engine, events, "models.download", node="test.files", checkpoints=["large"])
    home, files, cdn = (hub.base(name).removeprefix("https://") for name in ("hub", "files", "cdn"))
    journal = next(Layout(engine.data).journals.glob("*model-download*.ndjson"))  # the only job
    lines = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines()]
    fetched = {e["file"]: e["hosts"] for e in lines if e.get("phase") == "downloading"}
    # A small file on the hub is sent to the hub's own cache; a large one elsewhere, to the cdn.
    assert fetched == {"config": [home, home], "large": [files, cdn]}


def test_checkpoints_are_checked(engine: Engine, pinned_node: dict[str, str]) -> None:
    reply = call(engine, "models.download", node="test.files", checkpoints=["huge"])
    assert reply["error"]["reason"] == "bad_params" and "huge" in reply["error"]["message"]
    assert call(engine, "models.download", node="nobody")["error"]["reason"] == "bad_params"


def test_a_file_that_fails_does_not_stop_the_others(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts
) -> None:
    hub.fault(pinned_node["config"], Replace(b"x" * len(SHARED)))
    end = run_job(engine, events, "models.download", node="test.files")
    assert (end["event"], end["kind"], end["reason"]) == ("model.failed", "download", "hash_mismatch")
    assert [f["outcome"] for f in end["files"]] == ["failed", "downloaded"]
    assert end["files"][0]["failure"]["reason"] == "hash_mismatch"


def test_a_re_pin_with_one_changed_file_fetches_only_that_file(
    engine: Engine, events: Events, hub: Hosts, make_node: MakeNode
) -> None:
    repo = hub.repo("Ruicheng/moge-2-vitl-normal")
    first = repo.commit({"model.pt": LARGE, "config.json": SHARED})
    files = [
        hub_pinned("model", repo.id, first, "model.pt", LARGE),
        hub_pinned("config", repo.id, first, "config.json", SHARED),
    ]
    node_with_files(make_node, "test.moge", files, files_by=None, params={})
    call(engine, "nodes.reload")
    assert run_job(engine, events, "models.download", node="test.moge")["event"] == "model.done"
    changed = SHARED + b"\n"
    second = repo.commit({"model.pt": LARGE, "config.json": changed})
    files = [
        hub_pinned("model", repo.id, second, "model.pt", LARGE),
        hub_pinned("config", repo.id, second, "config.json", changed),
    ]
    node_with_files(make_node, "test.moge", files, files_by=None, params={})
    call(engine, "nodes.reload")
    end = run_job(engine, events, "models.download", node="test.moge")
    assert [f["outcome"] for f in end["files"]] == ["present", "downloaded"]
    assert len(hub.served(LARGE)) == 1 and len(hub.served(changed)) == 1


def test_a_second_job_is_refused_busy_here_and_locked_from_another_process(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts, tmp_path: Path
) -> None:
    stall = Stall(at=100_000)
    hub.fault(pinned_node["small"], stall)
    started = call(engine, "models.download", node="test.files")["result"]["job"]
    refused = call(engine, "models.verify")["error"]
    assert (refused["kind"], refused["reason"], refused["retry"]) == ("store", "busy", True)
    stall.release.set()
    wait_end(events, started)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time\nfrom pathlib import Path\nfrom oneframe.files import FileLock\n"
            "lock = FileLock(Path(sys.argv[1]))\nassert lock.acquire()\n"
            "print('held', flush=True)\ntime.sleep(60)",
            str(lock_file(Layout(tmp_path / "data").locks, "models")),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "held"
        refused = call(engine, "models.download", node="test.files")["error"]
        assert (refused["kind"], refused["reason"]) == ("store", "locked")
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.parametrize("by", ["models.stop", "shutdown"])
def test_stop_sends_model_stopped_within_two_seconds_and_keeps_the_partial(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts, by: str
) -> None:
    hub.fault(pinned_node["small"], Stall(at=100_000))
    engine.models.transfer["policy"] = Policy(stall_s=60)  # the stall alone would hold it for a minute
    job = call(engine, "models.download", node="test.files")["result"]["job"]
    partial = Store(engine.data).partial(sha(SMALL))
    wait_until(lambda: partial.exists() and partial.stat().st_size >= 100_000)
    time.sleep(0.2)
    pressed = time.monotonic()
    if by == "models.stop":
        assert call(engine, "models.stop", job=job)["result"] == {"stopping": True, "job": job}
        end = wait_end(events, job)
    else:
        engine.shutdown(10)
        end = wait_end(events, job, 1)  # shutdown waited for it
    assert end["event"] == "model.stopped" and time.monotonic() - pressed < 2
    assert [f["outcome"] for f in end["files"]] == ["downloaded", "stopped"]
    assert partial.stat().st_size == 100_000
    journals = sorted(Layout(engine.data).journals.glob("*model-download*.ndjson"))
    last = json.loads(journals[-1].read_text(encoding="utf-8").splitlines()[-1])
    assert (last["event"], last["job"]) == ("model.stopped", job)


def test_verify_drops_a_changed_file_so_the_next_download_fetches_only_it(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts
) -> None:
    assert run_job(engine, events, "models.download", node="test.files")["event"] == "model.done"
    store = Store(engine.data)
    blob = store.blob(sha(SMALL))
    info = blob.stat()
    blob.chmod(0o644)
    blob.write_bytes(b"y" * len(SMALL))  # same size, and the same time below: only a hash sees it
    os.utime(blob, ns=(info.st_atime_ns, info.st_mtime_ns))
    assert store.presence(sha(SMALL), len(SMALL)).status == "present"
    end = run_job(engine, events, "models.verify", node="test.files")
    assert end["event"] == "model.done" and end["dropped"] == [sha(SMALL)]
    assert {f["name"]: f["outcome"] for f in end["files"]} == {
        "config": "verified",
        "small": "dropped",
        "large": "missing",
    }
    end = run_job(engine, events, "models.download", node="test.files")
    assert [f["outcome"] for f in end["files"]] == ["present", "downloaded"]
    assert len(hub.served(SMALL)) == 2 and len(hub.served(SHARED)) == 1
    assert run_job(engine, events, "models.verify")["dropped"] == []  # every file in the store


def test_a_newer_store_refuses_download_and_verify(engine: Engine, pinned_node: dict[str, str]) -> None:
    store_file = Layout(engine.data).store_file
    store_file.parent.mkdir(parents=True)
    store_file.write_text('{"format": 99}', encoding="utf-8")
    for method in ("models.download", "models.verify"):
        error = call(engine, method, node="test.files")["error"]
        assert (error["kind"], error["reason"]) == ("store", "newer_format")
    assert store_file.read_text(encoding="utf-8") == '{"format": 99}'


KILLED = textwrap.dedent(
    """
    import sys, threading
    from pathlib import Path
    from fixtures.hosts import client_context
    from oneframe.models.store import Store
    from oneframe.models.transport import Policy, Transfer

    url, partial, size, sha = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
    def progress(have, total):
        if have >= 200_000:
            print("half", flush=True)
            threading.Event().wait(60)  # killed here, in the middle of the file
    Transfer(url, partial, size, sha, threading.Event(), progress=progress, context=client_context,
             policy=Policy(stall_s=60), proxy=lambda host: None).run()
    """
)


def test_a_killed_engine_leaves_a_partial_the_next_download_resumes(
    engine: Engine, events: Events, pinned_node: dict[str, str], hub: Hosts
) -> None:
    store = Store(engine.data)
    partial = store.partial(sha(LARGE))
    tests = str(Path(__file__).parent)
    child = subprocess.Popen(
        [sys.executable, "-c", KILLED, pinned_node["large"], str(partial), str(len(LARGE)), sha(LARGE)],
        stdout=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": os.pathsep.join([tests, os.environ.get("PYTHONPATH", "")])},
    )
    assert child.stdout is not None and child.stdout.readline().strip() == "half"
    child.kill()
    child.wait()
    kept = partial.stat().st_size
    assert kept >= 200_000
    end = run_job(engine, events, "models.download", node="test.files", checkpoints=["large"])
    assert [(f["name"], f["outcome"]) for f in end["files"]] == [
        ("config", "downloaded"),
        ("large", "downloaded"),
    ]
    checking = [e for e in events if e.get("phase") == "checking" and e["file"] == "large"]
    assert checking and checking[0]["bytes"] == kept
    assert hub.requests("cdn")[-1].headers["range"] == f"bytes={kept}-"
    assert store.presence(sha(LARGE), len(LARGE)).status == "present"


def test_a_file_the_system_refuses_to_read_fails_alone_and_is_no_engine_bug(
    engine: Engine, events: Events, pinned_node: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from oneframe.models import jobs

    assert run_job(engine, events, "models.download", node="test.files")["event"] == "model.done"
    real = jobs.hash_file

    def held(path: Path, *args: Any) -> str:
        if path.name == sha(SHARED):
            raise PermissionError(
                13, "The process cannot access the file because it is being used", str(path)
            )
        return real(path, *args)

    monkeypatch.setattr(jobs, "hash_file", held)
    end = run_job(engine, events, "models.verify", node="test.files")
    assert (end["event"], end["kind"]) == ("model.failed", "store")
    assert {f["name"]: f["outcome"] for f in end["files"]} == {
        "config": "failed",
        "small": "verified",
        "large": "missing",
    }
