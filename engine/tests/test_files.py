"""The one JSON writer and the operating-system locks (spec 006, task 2)."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from oneframe.files import FileLock, LockBusy, NewerFormat, format_of, is_free, lock_file, write_json
from oneframe.memory import STORE_VERSION, LearnedStore, StoreKey


def test_a_write_lands_whole_and_leaves_no_temporary(tmp_path: Path) -> None:
    target = tmp_path / "deep" / "settings.json"
    write_json(target, {"format": 1, "x": "é"})
    write_json(target, {"format": 1, "x": 2}, indent=2)
    assert json.loads(target.read_text(encoding="utf-8")) == {"format": 1, "x": 2}
    assert [p.name for p in target.parent.iterdir()] == ["settings.json"]


def test_a_newer_file_is_never_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "learned.json"
    target.write_text('{"version": 9, "kept": true}', encoding="utf-8")
    before = target.read_bytes()
    with pytest.raises(NewerFormat) as caught:
        write_json(target, {"version": 1}, key="version")
    assert (caught.value.found, caught.value.known) == (9, 1)
    assert target.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["learned.json"]


def test_an_older_or_broken_file_is_replaced(tmp_path: Path) -> None:
    target = tmp_path / "f.json"
    target.write_text('{"format": 0}', encoding="utf-8")
    write_json(target, {"format": 1})
    target.write_text("{not json", encoding="utf-8")
    write_json(target, {"format": 1})
    assert format_of(json.loads(target.read_text(encoding="utf-8"))) == 1


def test_the_writer_must_be_told_the_format(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="format"):
        write_json(tmp_path / "f.json", {"x": 1})


def test_format_of_reads_only_whole_numbers() -> None:
    assert format_of({"format": 2}) == 2
    assert format_of({}, missing=1) == 1
    assert format_of({}) is None
    assert format_of({"format": "2"}, missing=1) is None
    assert format_of({"format": True}) is None
    assert format_of([1]) is None


def test_learned_memory_in_a_newer_format_is_left_alone_and_not_used(tmp_path: Path) -> None:
    path = tmp_path / "memory" / "learned.json"
    path.parent.mkdir()
    newer = {"version": STORE_VERSION + 1, "clock": 5, "entries": {"x|1|m||cuda": {"ratios": [{"ratio": 9}]}}}
    path.write_text(json.dumps(newer), encoding="utf-8")
    before = path.read_bytes()
    store = LearnedStore(tmp_path)
    key = StoreKey("x", "1", "m", "")
    assert store.view(key).corrections == {}
    store.record(key, "cuda", settings="a", ok=True, need_mb=100.0, working_mb=100.0, weights_mb=0.0)
    assert store.newer == STORE_VERSION + 1
    assert path.read_bytes() == before
    assert store.view(key).corrections == {"cuda": 1.0}  # learned in memory meanwhile


def test_a_lock_is_held_once(tmp_path: Path) -> None:
    path = lock_file(tmp_path / "locks", "runtime-tiny")
    first, second = FileLock(path), FileLock(path)
    assert first.acquire()
    assert not second.acquire()  # in this process too
    assert not is_free(path)
    with pytest.raises(LockBusy, match="in use"), second:
        pass
    first.release()
    assert is_free(path)
    with second:
        assert second.held
    assert path.exists()  # a lock file is never deleted


def test_lock_names_are_safe_file_names(tmp_path: Path) -> None:
    for bad in ("", "../x", "a/b", "A", "x y"):
        with pytest.raises(ValueError):
            lock_file(tmp_path, bad)


HOLDER = textwrap.dedent(
    """
    import sys, time
    from pathlib import Path
    from oneframe.files import FileLock
    lock = FileLock(Path(sys.argv[1]))
    assert lock.acquire()
    print("held", flush=True)
    time.sleep(600)
    """
)


def test_a_dead_holder_releases_its_lock(tmp_path: Path) -> None:
    path = lock_file(tmp_path, "held")
    holder = subprocess.Popen(
        [sys.executable, "-c", HOLDER, str(path)], stdout=subprocess.PIPE, text=True, encoding="utf-8"
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "held"
        assert not FileLock(path).acquire()
    finally:
        holder.kill()
        holder.wait(timeout=30)
        if holder.stdout is not None:
            holder.stdout.close()
    deadline = time.monotonic() + 30  # Windows releases a dead process's locks a little late
    while not is_free(path):
        assert time.monotonic() < deadline, "the lock outlived its holder"
        time.sleep(0.1)
