"""Spec 003: the store (requirement 2; AC2's first half, AC6's newer store and records). One copy of
each file by sha256, read-only, present only once checked; a newer or unreadable store or record is
left as it is; a delete the system refuses keeps the file and its record."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from oneframe import files
from oneframe.layout import Layout
from oneframe.models import store as store_module
from oneframe.models.store import Store, StoreRefused

DATA = b"weights " * 1000
SHA = hashlib.sha256(DATA).hexdigest()


def _placed(tmp_path: Path) -> Store:
    store = Store(tmp_path / "data")
    store.claim()
    partial = store.partial(SHA)
    partial.parent.mkdir(parents=True)
    partial.write_bytes(DATA)
    store.place(partial, SHA)
    return store


def _refused(reason: str) -> pytest.RaisesExc[StoreRefused]:
    return pytest.raises(StoreRefused, check=lambda e: e.reason == reason)


def test_a_placed_file_is_stored_once_by_sha256_read_only_and_present(tmp_path: Path) -> None:
    store = _placed(tmp_path)
    blob = Layout(tmp_path / "data").blobs / SHA[:2] / SHA
    assert blob.read_bytes() == DATA and not blob.stat().st_mode & stat.S_IWRITE  # root ignores it
    assert not store.partial(SHA).exists()
    assert store.presence(SHA, len(DATA)).status == "present"
    assert store.stored() == [SHA]
    record = json.loads(store.record_path(SHA).read_text(encoding="utf-8"))
    assert (record["format"], record["size"], record["mtime_ns"]) == (1, len(DATA), blob.stat().st_mtime_ns)
    assert json.loads(Layout(tmp_path / "data").store_file.read_text(encoding="utf-8")) == {"format": 1}


def test_a_changed_file_reads_as_changed_without_hashing(tmp_path: Path) -> None:
    store = _placed(tmp_path)
    blob = store.blob(SHA)
    info = blob.stat()
    blob.chmod(stat.S_IREAD | stat.S_IWRITE)
    os.utime(blob, ns=(info.st_atime_ns, info.st_mtime_ns + 10**9))
    assert store.presence(SHA, len(DATA)) == store_module.Presence(
        "changed", 0, "it was modified after it was checked"
    )
    blob.write_bytes(DATA[:-1])
    assert store.presence(SHA, len(DATA)).status == "changed"


def test_a_partial_and_nothing_read_as_such(tmp_path: Path) -> None:
    store = Store(tmp_path / "data")
    assert store.presence(SHA, len(DATA)) == store_module.Presence("missing", 0)
    store.partial(SHA).parent.mkdir(parents=True)
    store.partial(SHA).write_bytes(DATA[:100])
    assert store.presence(SHA, len(DATA)) == store_module.Presence("partial", 100)
    assert store.url_partial("https://x.test/a").name.startswith("url-")


def test_an_unreadable_record_is_rebuilt_by_hashing_and_a_bad_file_is_dropped(tmp_path: Path) -> None:
    store = _placed(tmp_path)
    store.record_path(SHA).write_text("{not json", encoding="utf-8")
    assert store.presence(SHA, len(DATA)).status == "changed"
    assert store.adopt(SHA) is True
    assert store.presence(SHA, len(DATA)).status == "present"
    blob = store.blob(SHA)
    blob.chmod(stat.S_IREAD | stat.S_IWRITE)
    blob.write_bytes(b"x" * len(DATA))
    store.drop_record(SHA)
    assert store.adopt(SHA) is False
    assert not blob.exists() and not store.record_path(SHA).exists()


def test_a_newer_record_is_left_as_it_is_and_its_file_is_not_present(tmp_path: Path) -> None:
    store = _placed(tmp_path)
    newer = {"format": 2, "sha256": SHA, "size": len(DATA), "mtime_ns": 0, "more": "of the future"}
    store.record_path(SHA).write_text(json.dumps(newer), encoding="utf-8")
    assert store.presence(SHA, len(DATA)).status == "newer"
    assert store.adopt(SHA) is False
    with _refused("newer_format"):
        store.drop(SHA)
    partial = store.partial(SHA)
    partial.write_bytes(DATA)
    with _refused("newer_format"):
        store.place(partial, SHA)
    assert json.loads(store.record_path(SHA).read_text(encoding="utf-8")) == newer
    assert store.blob(SHA).read_bytes() == DATA


@pytest.mark.parametrize(
    ("text", "reason"), [('{"format": 2}', "newer_format"), ("{}", "unreadable"), ("[", "unreadable")]
)
def test_a_newer_or_unreadable_store_refuses_changes_and_its_files_are_not_present(
    tmp_path: Path, text: str, reason: str
) -> None:
    store = _placed(tmp_path)
    store.layout.store_file.write_text(text, encoding="utf-8")
    with _refused(reason):
        store.claim()
    assert store.presence(SHA, len(DATA)).status in ("newer", "unreadable")
    assert store.layout.store_file.read_text(encoding="utf-8") == text


def test_drop_deletes_the_file_then_its_record(tmp_path: Path) -> None:
    store = _placed(tmp_path)
    store.drop(SHA)
    assert store.stored() == [] and not store.record_path(SHA).exists()


def test_a_delete_the_system_refuses_keeps_the_file_and_its_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _placed(tmp_path)
    real = Path.unlink

    def refuse(self: Path, missing_ok: bool = False) -> None:
        if self.name == SHA:
            raise PermissionError(13, "The process cannot access the file", str(self))
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", refuse)
    with _refused("os_refused"):
        store.drop(SHA)
    assert store.blob(SHA).exists() and store.record_path(SHA).exists()


def test_placing_waits_on_windows_while_another_program_holds_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target = tmp_path / "a", tmp_path / "b"
    source.write_bytes(b"x")
    store = Store(tmp_path / "data")
    store.claim()
    partial = store.partial(SHA)
    partial.parent.mkdir(parents=True)
    partial.write_bytes(DATA)
    real = Path.replace
    tries: list[Path] = []

    def held(self: Path, other: Path) -> Path:
        tries.append(other)
        if len(tries) < 4:
            raise PermissionError(13, "being used by another process")
        return real(self, other)

    monkeypatch.setattr(Path, "replace", held)
    monkeypatch.setattr(files.time, "sleep", lambda _s: None)
    files.replace(source, target, windows=True)
    assert target.read_bytes() == b"x" and len(tries) == 4
    tries.clear()
    source.write_bytes(b"y")
    with pytest.raises(PermissionError):
        files.replace(source, target, windows=False)  # elsewhere, refused is refused
    assert len(tries) == 1

    # a hold past the wait, on any system: the store says os_refused, and keeps the partial
    monkeypatch.setattr(files, "MOVE_WAIT_S", 0.0)
    with _refused("os_refused") as caught:
        store.place(partial, SHA)
    assert caught.value.next == "An antivirus may be scanning it; try again in a moment."
    assert partial.read_bytes() == DATA and not store.blob(SHA).exists()


def test_paths_are_built_only_from_a_sha256(tmp_path: Path) -> None:
    store = Store(tmp_path)
    for bad in ["../" + "a" * 61, "A" * 64, "a" * 63]:
        with pytest.raises(ValueError):
            store.blob(bad)
    with pytest.raises(ValueError):
        store.drop_partial(tmp_path / "elsewhere")
