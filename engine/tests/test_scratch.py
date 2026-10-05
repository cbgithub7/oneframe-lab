"""Processes on one root leave each other's work alone (AC5 of spec 006): each keeps its temporary
and job folders in a locked folder of its own under cache/tmp/, a start removes only the folders
whose lock is free, and installing or removing a runtime holds that runtime's lock."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

import pytest
from conftest import NO_GPU

from oneframe import BUILTIN_NODES_DIR, scratch
from oneframe.files import is_free, lock_file
from oneframe.layout import Layout
from oneframe.scratch import Scratch, for_tool, sweep
from oneframe.server import Engine


def test_each_scratch_folder_is_its_own_and_goes_when_released(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    first, second = Scratch.claim(layout), Scratch.claim(layout)
    assert (first.folder, second.folder) == (layout.tmp / "0", layout.tmp / "1")
    job = first.make("job-")
    assert job.parent == first.folder and job.is_dir()
    first.release()
    assert not first.folder.exists() and second.folder.is_dir()
    assert Scratch.claim(layout).folder == layout.tmp / "0"  # numbers are reused
    assert lock_file(layout.locks, "tmp-0").exists()  # and lock files kept


def test_a_sweep_removes_only_what_no_live_process_holds(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    live = Scratch.claim(layout)
    (live.folder / "work.txt").write_text("busy", encoding="utf-8")
    dead = layout.tmp / "7"
    (dead / "job").mkdir(parents=True)
    legacy = layout.tmp / "0123456789abcdef-1234abcd"  # a run folder from before spec 006
    legacy.mkdir()
    gone = sweep(layout)
    assert sorted(gone) == sorted([dead, legacy])
    assert (live.folder / "work.txt").read_text(encoding="utf-8") == "busy"


def test_a_tool_makes_its_folder_the_process_temporary_folder_until_it_ends(tmp_path: Path) -> None:
    before = (tempfile.gettempdir(), os.environ.get("TMPDIR"))
    with for_tool(Layout(tmp_path)) as mine:
        assert Path(tempfile.gettempdir()) == mine.folder
        assert os.environ["TMP"] == os.environ["TEMP"] == os.environ["TMPDIR"] == str(mine.folder)
        made = Path(tempfile.mkdtemp())
        assert made.parent == mine.folder
    assert not mine.folder.exists()
    assert (tempfile.gettempdir(), os.environ.get("TMPDIR")) == before


def test_an_engine_keeps_its_runs_and_jobs_in_its_own_folder(tmp_path: Path) -> None:
    engine = Engine(tmp_path, [BUILTIN_NODES_DIR], lambda _m: None, profile=lambda: NO_GPU)
    try:
        assert engine.scratch.folder.parent == Layout(tmp_path).tmp
        assert engine.cache.tmp.parent == engine.scratch.folder
        assert engine.scheduler.tmp_root == engine.scratch.folder
    finally:
        engine.shutdown()
    assert not engine.scratch.folder.exists()


HELPER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from oneframe.files import FileLock, lock_file
    from oneframe.layout import Layout
    from oneframe.scratch import Scratch
    layout = Layout(Path(sys.argv[1]))
    mine = Scratch.claim(layout)
    (mine.folder / "work.txt").write_text("busy", encoding="utf-8")
    runtime = FileLock(lock_file(layout.locks, "runtime-tiny"))
    assert runtime.acquire()
    print(mine.folder, flush=True)
    time.sleep(600)
    """
)


def _wait_free(*paths: Path) -> None:
    deadline = time.monotonic() + 30  # Windows releases a dead process's locks a little late
    while not all(is_free(p) for p in paths):
        assert time.monotonic() < deadline, "a lock outlived its holder"
        time.sleep(0.1)


def test_ac5_two_processes_on_one_root(tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path) -> None:
    data = tmp_path / "data"
    layout = Layout(data)
    helper = subprocess.Popen(
        [sys.executable, "-c", HELPER, str(data)], stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )

    def start() -> Engine:
        return Engine(
            data,
            [BUILTIN_NODES_DIR],
            lambda _m: None,
            runtime_roots=[tiny.parent],
            uv=uv_exe,
            uv_home=uv_home,
            profile=lambda: NO_GPU,
        )

    try:
        assert helper.stdout is not None
        theirs = Path(helper.stdout.readline().strip())
        engine = start()
        try:
            assert (theirs / "work.txt").read_text(encoding="utf-8") == "busy"  # kept
            assert engine.scratch.folder != theirs
            for method in ("runtimes.install", "runtimes.remove"):
                reply = engine.handle({"id": 1, "method": method, "params": {"runtime": "tiny"}}) or {}
                assert reply["error"]["reason"] == "locked", (method, reply)
            assert engine.runtimes.installing() is None  # the refused install claimed nothing
        finally:
            engine.shutdown()
    finally:
        helper.kill()
        helper.wait(timeout=30)
        if helper.stdout is not None:
            helper.stdout.close()

    _wait_free(lock_file(layout.locks, f"tmp-{theirs.name}"), lock_file(layout.locks, "runtime-tiny"))
    engine = start()
    try:
        assert not theirs.exists() or theirs == engine.scratch.folder  # the dead helper's folder went
        assert not (theirs / "work.txt").exists()
        done = engine.runtimes.install("tiny")
        assert done is not None and engine.runtimes.list()["runtimes"][0]["status"] == "installed"
        assert is_free(lock_file(layout.locks, "runtime-tiny"))  # released when the install ended
    finally:
        engine.shutdown()


def test_a_dead_processs_folder_that_cannot_be_removed_is_passed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = Layout(tmp_path)
    stuck = layout.tmp / "0"
    (stuck / "job-x").mkdir(parents=True)  # its lock is free: its process died
    real_remove = scratch._remove
    monkeypatch.setattr(scratch, "_remove", lambda entry: False if entry == stuck else real_remove(entry))
    mine = Scratch.claim(layout)  # Windows refuses to delete a folder something still holds open
    assert mine.folder == layout.tmp / "1"
    assert stuck.exists() and is_free(lock_file(layout.locks, "tmp-0"))
    mine.release()
