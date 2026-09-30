"""Pinned archives: only the pinned bytes are kept, and nothing is unpacked outside its folder (AC8)."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from oneframe import archives


class _Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def _opener(data: bytes, calls: list[str] | None = None) -> Callable[..., _Response]:
    def open_url(url: str, **_kw: Any) -> _Response:
        if calls is not None:
            calls.append(url)
        return _Response(data)

    return open_url


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_a_source_with_the_wrong_sha256_is_refused(tmp_path: Path) -> None:
    dest = tmp_path / "downloads" / "up.tar.gz"
    with pytest.raises(archives.ArchiveError) as caught:
        archives.download(
            "https://example.test/up.tar.gz", "0" * 64, dest, opener=_opener(b"not the pinned file")
        )
    assert f"its sha256 is {_sha(b'not the pinned file')}, the pin is {'0' * 64}" in str(caught.value)
    assert list((tmp_path / "downloads").iterdir()) == []  # neither the file nor a .part is kept


def test_a_pinned_download_is_kept_and_not_fetched_again(tmp_path: Path) -> None:
    data = b"x" * (3 * archives.CHUNK + 5)
    dest = tmp_path / "up.tar.gz"
    calls: list[str] = []
    seen: list[tuple[int, int | None]] = []
    archives.download(
        "https://example.test/up",
        _sha(data),
        dest,
        lambda d, t: seen.append((d, t)),
        None,
        _opener(data, calls),
    )
    assert dest.read_bytes() == data
    assert seen[-1] == (len(data), len(data)) and len(seen) == 4
    archives.download("https://example.test/up", _sha(data), dest, opener=_opener(data, calls))
    assert calls == ["https://example.test/up"]


def test_a_file_on_disk_that_does_not_match_is_fetched_again(tmp_path: Path) -> None:
    data = b"pinned"
    dest = tmp_path / "up.tar.gz"
    dest.write_bytes(b"stale")
    archives.download("https://example.test/up", _sha(data), dest, opener=_opener(data))
    assert dest.read_bytes() == data


def test_stop_during_a_download_keeps_nothing(tmp_path: Path) -> None:
    data = b"y" * (2 * archives.CHUNK)
    dest = tmp_path / "up.tar.gz"
    with pytest.raises(archives.Stopped):
        archives.download("https://example.test/up", _sha(data), dest, None, lambda: True, _opener(data))
    assert list(tmp_path.iterdir()) == []


def _tar(path: Path, members: list[tarfile.TarInfo | tuple[str, bytes]]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for member in members:
            if isinstance(member, tarfile.TarInfo):
                tar.addfile(member)
            else:
                name, data = member
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
    return path


def _link(name: str, target: str, kind: bytes = tarfile.SYMTYPE) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = target
    return info


def test_a_good_archive_unpacks_without_its_top_folder(tmp_path: Path) -> None:
    archive = _tar(
        tmp_path / "up.tar.gz",
        [
            ("repo-abc123/pkg/__init__.py", b"VALUE = 1\n"),
            ("repo-abc123/README", b"hi"),
            _link("repo-abc123/pkg/alias.py", "__init__.py"),
        ],
    )
    out = archives.extract(archive, tmp_path / "src" / "up")
    assert (out / "pkg" / "__init__.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (out / "pkg" / "alias.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert sorted(p.name for p in (tmp_path / "src").iterdir()) == ["up"]  # no unpack folder left


def test_a_good_zip_unpacks_too(tmp_path: Path) -> None:
    archive = tmp_path / "up.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("repo-abc/mod.py", "X = 2\n")
    out = archives.extract(archive, tmp_path / "up")
    assert (out / "mod.py").read_text(encoding="utf-8") == "X = 2\n"


ESCAPES: list[tuple[str, list[tarfile.TarInfo | tuple[str, bytes]]]] = [
    ("a .. step", [("repo/ok.py", b""), ("repo/../../evil.py", b"x")]),
    ("an absolute path", [("/tmp/evil.py", b"x")]),
    ("a drive letter", [("C:/evil.py", b"x")]),
    ("a symlink out", [("repo/ok.py", b""), _link("repo/out", "../../outside")]),
    ("an absolute symlink", [_link("repo/out", "/etc/passwd")]),
    ("a hard link out", [("repo/ok.py", b""), _link("repo/hard", "../outside", tarfile.LNKTYPE)]),
]


@pytest.mark.parametrize(("how", "members"), ESCAPES, ids=[e[0] for e in ESCAPES])
def test_archive_members_that_escape_are_refused(
    tmp_path: Path, how: str, members: list[tarfile.TarInfo | tuple[str, bytes]]
) -> None:
    archive = _tar(tmp_path / "bad.tar.gz", members)
    target = tmp_path / "deep" / "src" / "up"
    with pytest.raises(archives.ArchiveError, match="outside its folder"):
        archives.extract(archive, target)
    assert not target.exists(), how
    assert list((tmp_path / "deep" / "src").iterdir()) == []
    assert not (tmp_path / "evil.py").exists() and not (tmp_path / "deep" / "evil.py").exists()


@pytest.mark.parametrize(
    "name", ["../evil.py", "repo/../../evil.py", "C:/evil.py", "C:\\evil.py", "/evil.py"]
)
def test_zip_members_that_escape_are_refused(tmp_path: Path, name: str) -> None:
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        zipped.writestr("repo/ok.py", "")
        zipped.writestr(name, "x")
    with pytest.raises(archives.ArchiveError, match="outside its folder"):
        archives.extract(archive, tmp_path / "src" / "up")
    assert list((tmp_path / "src").iterdir()) == []


def test_a_device_in_an_archive_is_refused(tmp_path: Path) -> None:
    fifo = tarfile.TarInfo("repo/pipe")
    fifo.type = tarfile.FIFOTYPE
    archive = _tar(tmp_path / "bad.tar.gz", [("repo/ok.py", b""), fifo])
    with pytest.raises(archives.ArchiveError, match="device or pipe"):
        archives.extract(archive, tmp_path / "up")


def test_unpacking_over_an_existing_folder_is_refused(tmp_path: Path) -> None:
    archive = _tar(tmp_path / "up.tar.gz", [("repo/ok.py", b"")])
    (tmp_path / "up").mkdir()
    with pytest.raises(archives.ArchiveError, match="already there"):
        archives.extract(archive, tmp_path / "up")
