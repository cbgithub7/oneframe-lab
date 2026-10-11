"""The files a node reads, declared in its manifest (spec 003, requirement 1):

    "files_by": "checkpoint",
    "files": [
      {"name": "weights", "hf": "Ruicheng/moge-3-vitl", "revision": "<full commit>",
       "path": "model.pt", "size": 1314651781, "sha256": "<64 hex>", "format": "pickle",
       "checkpoints": ["vitl"]},
      {"name": "depth_pro", "url": "https://ml-site.cdn-apple.com/models/depth-pro/depth_pro.pt",
       "size": 1904446787, "sha256": "<64 hex>", "format": "pickle"}
    ]

A file's size and sha256 are its identity: the source, a Hugging Face repository at a full commit
or an https URL, is only where to fetch it, so a file two nodes pin is stored once. A file without
`checkpoints` is read by every run; one with them only when the node's `files_by` choice param is
one of them. `npm run models:pin` fills in the commit, sizes, sha256s, formats and `gated`, so a
manifest is written with sources and checked once pinned: a manifest that is not refuses to load.
"""

from __future__ import annotations

import re
import urllib.parse
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from oneframe.manifest import Param

FORMATS = ("safetensors", "pickle", "other")  # pickle includes torch's zip archives
KEYS = {"name", "hf", "revision", "path", "url", "size", "sha256", "format", "checkpoints", "gated"}
PRECISION = "precision"  # never keys files: the engine picks it, and the weights are the same bytes

_NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]*$")
_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REPO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
# Names Windows keeps for devices, with any extension: CON, NUL.txt, COM1, LPT9.bin, ...
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[0-9¹²³]|lpt[0-9¹²³])(\..*)?$", re.IGNORECASE)
_REFUSED_CHARS = set('<>"|?*')
_DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class FileSpec:
    """One file a node reads: its identity (size, sha256), where to fetch it, and when."""

    name: str
    size: int
    sha256: str
    format: str
    url: str | None = None  # an https URL
    repo: str | None = None  # a Hugging Face repository
    revision: str | None = None  # its full commit
    path: str | None = None  # the file's path in the repository
    checkpoints: tuple[str, ...] = ()  # empty: every run reads it
    gated: str | None = None  # the page where its licence is accepted, if its source is gated

    def source(self, endpoint: str) -> str:
        """Where an attempt starts: the URL, or the hub's resolve URL at the pinned commit."""
        if self.url is not None:
            return self.url
        path = urllib.parse.quote(str(self.path))
        return f"{endpoint.rstrip('/')}/{self.repo}/resolve/{self.revision}/{path}"

    def wanted_by(self, checkpoint: str | None) -> bool:
        """Whether a run at `checkpoint` reads this file (None: a node without files_by)."""
        return not self.checkpoints or checkpoint in self.checkpoints


def unsafe_path(path: str) -> str | None:
    """Why a path in a repository cannot be laid out as a file on every system, or None."""
    if not path:
        return "is empty"
    if "\\" in path:
        return "uses a backslash; paths in a repository use /"
    if path.startswith("/") or _DRIVE.match(path):
        return "is absolute"
    if ":" in path:
        return "holds a ':', which Windows refuses"
    for part in path.split("/"):
        if part in ("", "."):
            return "has an empty step"
        if part == "..":
            return "steps out of its folder with '..'"
        if part[-1] in ". ":
            return f"has {part!r}, which ends with a dot or a space, which Windows drops"
        if _RESERVED.match(part):
            return f"has {part!r}, a name Windows keeps for a device"
        if any(ord(c) < 32 or c in _REFUSED_CHARS for c in part):
            return f"has {part!r}, with a character Windows refuses"
    return None


def parse(
    data: Mapping[str, Any],
    params: Mapping[str, Param],
    where: str,
    weights_by: Any,
    problems: list[str],
) -> tuple[tuple[FileSpec, ...], str | None]:
    """A manifest's `files` and `files_by`, with every problem added to `problems`. `where` is the
    node's run.where; `weights_by`, its memory model's, if any."""
    rows = data.get("files")
    files_by = data.get("files_by")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        problems.append("files should be a list of files")
        rows = []
    if rows and where == "engine":
        problems.append("files: a node that runs in the engine cannot read files; run it in a runtime")
    by = _files_by(files_by, params, weights_by, bool(rows), problems)
    choices = params[by].choices if by is not None and by in params else ()
    found: list[FileSpec] = []
    names: set[str] = set()
    paths: dict[tuple[str, str], str] = {}  # (repo, path in lower case) -> the path as written
    for index, row in enumerate(rows):
        spec = _file(index, row, files_by, choices, problems)
        if spec is not None:
            found.append(spec)
        if not isinstance(row, dict):
            continue
        # Names and paths are compared as written, so a file with other problems still counts.
        name, repo, path = row.get("name"), row.get("hf"), row.get("path")
        if isinstance(name, str):
            if name in names:
                problems.append(f"files: two files are named {name!r}")
            names.add(name)
        if isinstance(repo, str) and isinstance(path, str):
            other = paths.setdefault((repo.lower(), path.lower()), path)
            if other != path:
                problems.append(
                    f"files.{name}: {path!r} and {other!r} in {repo} differ only in case, "
                    "which Windows cannot keep apart"
                )
    return tuple(found), by


def _files_by(
    files_by: Any, params: Mapping[str, Param], weights_by: Any, has_files: bool, problems: list[str]
) -> str | None:
    if files_by is None:
        if has_files and isinstance(weights_by, str):
            _affects_output(weights_by, params, "memory.weights_by", problems)
        return None
    if files_by == PRECISION:
        problems.append("files_by cannot be precision: every precision reads the same files")
        return None
    param = params.get(files_by) if isinstance(files_by, str) else None
    if param is None or param.type != "choice":
        problems.append(f"files_by {files_by!r} should name one of the node's choice params")
        return None
    if param.default is None:
        problems.append(f"files_by {files_by!r} needs a default: the checkpoint a download fetches")
    if weights_by is not None and weights_by != files_by:
        problems.append(
            f"files_by {files_by!r} and memory.weights_by {weights_by!r} should name the same param"
        )
    _affects_output(files_by, params, "files_by", problems)
    return files_by


def _affects_output(name: str, params: Mapping[str, Param], what: str, problems: list[str]) -> None:
    param = params.get(name)
    if param is not None and param.affects != "output":
        problems.append(
            f"{what} {name!r} must affect output: each checkpoint gives other results, so it is part of "
            "the cache key"
        )


def _file(
    index: int, row: Any, files_by: Any, choices: tuple[str, ...], problems: list[str]
) -> FileSpec | None:
    if not isinstance(row, dict):
        problems.append(f"files[{index}] should be an object")
        return None
    name = row.get("name")
    if not isinstance(name, str) or not _NAME.match(name):
        problems.append(f"files[{index}].name should be lower case letters, digits, '.', '_' or '-'")
        name = f"[{index}]"
    at = f"files.{name}" if not name.startswith("[") else f"files{name}"
    start = len(problems)
    for key in sorted(row.keys() - KEYS):
        problems.append(f"{at}: {key!r} is not part of a file")
    url, repo, revision, path = _source(at, row, problems)
    size = row.get("size")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        problems.append(f"{at}.size should be the file's size in bytes, a whole number (npm run models:pin)")
        size = 0
    sha = row.get("sha256")
    if not isinstance(sha, str) or not _SHA256.match(sha):
        problems.append(f"{at}.sha256 should be 64 lower-case hex digits (npm run models:pin)")
    kind = row.get("format")
    if kind not in FORMATS:
        problems.append(f"{at}.format should be one of {', '.join(FORMATS)} (npm run models:pin)")
    checkpoints = row.get("checkpoints")
    if checkpoints is None:
        checkpoints = []
    if not isinstance(checkpoints, list) or not all(isinstance(c, str) for c in checkpoints):
        problems.append(f"{at}.checkpoints should be a list of choices of files_by")
        checkpoints = []
    elif checkpoints and files_by is None:
        problems.append(f"{at}.checkpoints needs files_by, the choice param they are values of")
    elif files_by is not None:
        for choice in checkpoints:
            if choice not in choices:
                problems.append(f"{at}.checkpoints: {choice!r} is not a choice of {files_by}")
    gated = row.get("gated")
    if gated is False:
        gated = None
    if gated is not None and not (isinstance(gated, str) and gated.startswith("https://")):
        problems.append(f"{at}.gated should be false, or the https page where its licence is accepted")
        gated = None
    if len(problems) > start:
        return None
    return FileSpec(
        name=name,
        size=size,
        sha256=str(sha),
        format=str(kind),
        url=url,
        repo=repo,
        revision=revision,
        path=path,
        checkpoints=tuple(checkpoints),
        gated=gated,
    )


def _source(
    at: str, row: Mapping[str, Any], problems: list[str]
) -> tuple[str | None, str | None, str | None, str | None]:
    url, repo = row.get("url"), row.get("hf")
    if url is not None and repo is not None:
        problems.append(f"{at} names both hf and url; a file has one source")
        return None, None, None, None
    if url is not None:
        for key in ("revision", "path"):
            if key in row:
                problems.append(f"{at}.{key} belongs to an hf source, not a url")
        parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
        if parts is None or parts.scheme != "https" or not parts.hostname:
            problems.append(f"{at}.url should be an https URL")
            return None, None, None, None
        if parts.username is not None or parts.password is not None:
            problems.append(f"{at}.url should not hold a user name or password")
            return None, None, None, None
        return url, None, None, None
    if repo is None:
        problems.append(f"{at} needs a source: hf with revision and path, or url")
        return None, None, None, None
    ok = True
    if not isinstance(repo, str) or not _REPO.match(repo) or ".." in repo:
        problems.append(f"{at}.hf should be a Hugging Face repository, owner/name")
        ok = False
    revision = row.get("revision")
    if not isinstance(revision, str) or not _COMMIT.match(revision):
        problems.append(
            f"{at}.revision should be a full commit hash, not a branch or a tag (npm run models:pin)"
        )
        ok = False
    path = row.get("path")
    why = unsafe_path(path) if isinstance(path, str) else "should be the file's path in the repository"
    if why is not None:
        problems.append(f"{at}.path {path!r} {why}")
        ok = False
    if not ok:
        return None, None, None, None
    return None, str(repo), str(revision), str(path)
