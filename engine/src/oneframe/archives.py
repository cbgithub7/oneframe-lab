"""Pinned upstream archives: downloaded only as the exact bytes a runtime pins, and unpacked only
inside their own folder.

A runtime can name upstream code that is on no package index, as an archive at a pinned address
with its sha256. The download is written beside its final name and renamed into place only when its
hash matches, so a half-downloaded or altered file is never kept, and an archive already on disk
with the right hash is not fetched again.

Unpacking trusts nothing in the archive: a member with an absolute path, a drive letter or a `..`
step, and a link (symbolic or hard) whose target is outside the folder, are refused before anything
is written. The archive is unpacked into a temporary folder beside the target and renamed into
place, so a refused or interrupted unpack leaves nothing behind.
"""

from __future__ import annotations

import hashlib
import posixpath
import re
import shutil
import tarfile
import urllib.request
import uuid
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

CHUNK = 1 << 20
TIMEOUT_S = 60
_DRIVE = re.compile(r"^[A-Za-z]:")

Progress = Callable[[int, int | None], None]


class ArchiveError(RuntimeError):
    """An archive that is not the pinned file, or that would write outside its folder."""


class Stopped(RuntimeError):
    """Stop was pressed during a download."""


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(
    url: str,
    sha256: str,
    dest: Path,
    progress: Progress | None = None,
    should_stop: Callable[[], bool] | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> Path:
    """Fetch `url` to `dest`, keeping it only if its sha256 is `sha256`."""
    if dest.is_file():
        if sha256_of(dest) == sha256:
            return dest
        dest.unlink()
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with opener(url, timeout=TIMEOUT_S) as response, part.open("wb") as handle:
            length = response.headers.get("Content-Length")
            total = int(length) if length and str(length).isdigit() else None
            # read1 returns what has arrived, so progress and Stop are seen while bytes trickle in
            read = getattr(response, "read1", response.read)
            while True:
                if should_stop is not None and should_stop():
                    raise Stopped()
                chunk = read(CHUNK)
                if not chunk:
                    break
                digest.update(chunk)
                handle.write(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
        got = digest.hexdigest()
        if got != sha256:
            raise ArchiveError(
                f"{url} is not the file the runtime pins (its sha256 is {got}, the pin is {sha256}), "
                "so it was not kept."
            )
        part.replace(dest)
        return dest
    finally:
        part.unlink(missing_ok=True)


def _escapes(name: str) -> bool:
    """Whether a member name, taken relative to the folder, could land outside it."""
    text = name.replace("\\", "/")
    if text.startswith("/") or _DRIVE.match(text):
        return True
    return ".." in text.split("/")


def _link_escapes(member: str, target: str, hard: bool) -> bool:
    """A symlink's target is relative to the link's own folder; a hard link's to the archive root."""
    text = target.replace("\\", "/")
    if text.startswith("/") or _DRIVE.match(text):
        return True
    base = "" if hard else posixpath.dirname(member.replace("\\", "/"))
    resolved = posixpath.normpath(posixpath.join(base, text))
    return resolved == ".." or resolved.startswith("../")


def _check_tar(tar: tarfile.TarFile) -> None:
    for member in tar.getmembers():
        if _escapes(member.name):
            raise ArchiveError(f"The archive member {member.name!r} would be written outside its folder.")
        if member.issym() or member.islnk():
            if _link_escapes(member.name, member.linkname, hard=member.islnk()):
                kind = "hard link" if member.islnk() else "link"
                raise ArchiveError(
                    f"The archive member {member.name!r} is a {kind} to {member.linkname!r}, "
                    "outside its folder."
                )
        elif not (member.isfile() or member.isdir()):
            raise ArchiveError(
                f"The archive member {member.name!r} is a device or pipe, which a source never holds."
            )


def _check_zip(archive: zipfile.ZipFile) -> None:
    for name in archive.namelist():
        if _escapes(name):
            raise ArchiveError(f"The archive member {name!r} would be written outside its folder.")


def _single_top(folder: Path) -> Path:
    """The archive's one top folder, which GitHub's archives always have, or the folder itself."""
    entries = list(folder.iterdir())
    if len(entries) == 1 and entries[0].is_dir():
        return entries[0]
    return folder


def extract(archive: Path, dest: Path) -> Path:
    """Unpack a .tar.gz, .tgz, .tar or .zip into `dest`, which must not exist yet. A single top
    folder is taken as the root. Every member is checked before anything is written."""
    if dest.exists():
        raise ArchiveError(f"{dest} is already there; remove it before unpacking again.")
    dest.parent.mkdir(parents=True, exist_ok=True)
    work = dest.parent / f".{dest.name}.unpack-{uuid.uuid4().hex[:8]}"
    work.mkdir()
    try:
        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as zipped:
                _check_zip(zipped)
                zipped.extractall(work)
        elif tarfile.is_tarfile(archive):
            with tarfile.open(archive) as tar:
                _check_tar(tar)
                tar.extractall(work, filter="data")
        else:
            raise ArchiveError(f"{archive.name} is neither a tar nor a zip archive.")
        _single_top(work).replace(dest)
        return dest
    finally:
        shutil.rmtree(work, ignore_errors=True)
