"""Pinning a node's files (spec 003, requirement 8): `npm run models:pin -- <node>`.

A manifest is written with sources only: `hf` and `path` (with `revision` a branch, a tag or a
commit; `main` when left out), or a `url`. Pinning, for each file:

- resolves the repository's commit and canonical id, and whether it is gated, from the hub's API;
- fetches the file through the store, hashing it, unless the hub names its sha256 and the store
  already holds it, and reads its format from its content (a torch zip archive is a pickle,
  whatever its name);
- writes the commit, the canonical id, `gated`, the size, the sha256 and the format back.

Re-pinning to a new commit rewrites every entry of that source and reports each sha256 that
changed. A value that changed while its pin did not (a URL that now serves other bytes, a commit
whose file the hub describes otherwise) is reported and left as it was: the pin is the record of
what was checked, and only a person decides to trust the new bytes.

A URL is fetched again on every pin, since nothing but its bytes says whether it changed; a file at
a pinned commit is fetched only when the store does not hold it.
"""

from __future__ import annotations

import json
import struct
import threading
import urllib.parse
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe.manifest import ManifestError, load
from oneframe.models.store import Store
from oneframe.models.transport import Connector, DownloadFailed, Transfer

Say = Callable[[dict[str, Any]], None]
SAFETENSORS_HEADER_MAX = 100 * 2**20  # a header longer than this is not a safetensors file's


def sniff(path: Path) -> str:
    """A file's format, read from its content: `safetensors`, `pickle` (torch's zip archives and
    its older format included) or `other`."""
    with path.open("rb") as handle:
        head = handle.read(16)
    size = path.stat().st_size
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                if any(name.endswith("data.pkl") for name in archive.namelist()):
                    return "pickle"
        except OSError, zipfile.BadZipFile:
            return "other"
        return "other"
    if len(head) >= 2 and head[0] == 0x80 and 2 <= head[1] <= 5:
        return "pickle"
    if len(head) >= 9:
        (length,) = struct.unpack("<Q", head[:8])
        if 0 < length <= SAFETENSORS_HEADER_MAX and length + 8 <= size and head[8:9] == b"{":
            return "safetensors"
    return "other"


@dataclass
class Report:
    node: str
    path: Path
    files: list[dict[str, Any]] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)  # a new pin's new sha256
    drifted: list[dict[str, Any]] = field(default_factory=list)  # changed under an unchanged pin: left
    failed: list[dict[str, Any]] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # the manifest's, once written

    def to_json(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "manifest": str(self.path),
            "files": self.files,
            "changed": self.changed,
            "drifted": self.drifted,
            "failed": self.failed,
            "problems": self.problems,
        }


def find_manifest(roots: list[Path], node: str) -> Path | None:
    """`<root>/<node>/node.json`, or any node.json there whose id is `node`, read as it is: a
    manifest that is not pinned yet does not pass the check."""
    for root in roots:
        direct = root / node / "node.json"
        if direct.is_file():
            return direct
    for root in roots:
        for path in sorted(root.glob("*/node.json")):
            try:
                if json.loads(path.read_text(encoding="utf-8")).get("id") == node:
                    return path
            except OSError, ValueError, AttributeError:
                continue
    return None


def pin(
    path: Path,
    store: Store,
    endpoint: str,
    stop: threading.Event,
    say: Say,
    transfer: dict[str, Any] | None = None,
) -> Report:
    """Pin every file of the manifest at `path`, through `store`, and write the manifest back. The
    caller holds the store's lock."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw.get("files") or []
    report = Report(str(raw.get("id", path.parent.name)), path)
    options = dict(transfer or {})
    connector = Connector(
        stop, options.get("policy"), **{k: v for k, v in options.items() if k in ("context", "proxy")}
    )
    endpoint = endpoint.rstrip("/")
    resolved: dict[tuple[str, str], tuple[str, str, Any]] = {}
    described: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("hf"), str):
            continue
        key = (row["hf"].lower(), str(row.get("revision") or "main"))
        if key in resolved:
            continue
        revision = urllib.parse.quote(key[1], safe="")
        info = connector.get_json(f"{endpoint}/api/models/{row['hf']}/revision/{revision}")
        resolved[key] = (str(info["id"]), str(info["sha"]), info.get("gated") or False)
        say(
            {
                "event": "pin.resolved",
                "repo": row["hf"],
                "revision": key[1],
                "id": info["id"],
                "commit": info["sha"],
            }
        )
    for (repo_lower, revision), (canonical, commit, _gated) in resolved.items():
        paths = [
            str(r.get("path"))
            for r in rows
            if isinstance(r, dict)
            and isinstance(r.get("hf"), str)
            and (r["hf"].lower(), str(r.get("revision") or "main")) == (repo_lower, revision)
        ]
        entries = connector.get_json(
            f"{endpoint}/api/models/{canonical}/paths-info/{commit}", {"paths": paths}
        )
        described[(canonical, commit)] = {str(e["path"]): e for e in entries if isinstance(e, dict)}

    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name"))
        try:
            outcome = _pin_one(row, name, endpoint, resolved, described, store, stop, say, options, report)
        except DownloadFailed as exc:
            report.failed.append({"name": name, **exc.to_json()})
            say({"event": "pin.failed", "file": name, **exc.to_json()})
            continue
        report.files.append(outcome)
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    try:
        load(path)
    except ManifestError as exc:
        report.problems = exc.problems
    return report


def _pin_one(
    row: dict[str, Any],
    name: str,
    endpoint: str,
    resolved: dict[tuple[str, str], tuple[str, str, Any]],
    described: dict[tuple[str, str], dict[str, dict[str, Any]]],
    store: Store,
    stop: threading.Event,
    say: Say,
    options: dict[str, Any],
    report: Report,
) -> dict[str, Any]:
    recorded_sha, recorded_size = row.get("sha256"), row.get("size")
    size_hint: int | None = None
    sha_hint: str | None = None
    if isinstance(row.get("hf"), str):
        given = str(row.get("revision") or "main")
        canonical, commit, gated = resolved[(row["hf"].lower(), given)]
        unchanged = given == commit and recorded_sha is not None
        entry = described[(canonical, commit)].get(str(row.get("path")))
        if entry is None:
            raise DownloadFailed(f"{canonical} has no {row.get('path')} at {commit}.", "not_found")
        size_hint = int(entry["size"])
        lfs = entry.get("lfs")
        sha_hint = str(lfs["oid"]) if isinstance(lfs, dict) and lfs.get("oid") else None
        source = f"{endpoint}/{canonical}/resolve/{commit}/{urllib.parse.quote(str(row['path']))}"
        # Known before any byte: kept even when the download is refused (a gated file, say).
        row.update(hf=canonical, gated=f"{endpoint}/{canonical}" if gated else False)
        new = {"revision": commit}
    else:
        source = str(row.get("url"))
        unchanged = recorded_sha is not None
        new = {}
    if unchanged and sha_hint is not None and sha_hint != recorded_sha:
        return _drifted(report, say, name, recorded_sha, sha_hint, recorded_size, size_hint)
    if sha_hint is not None and store.presence(sha_hint, size_hint or 0).status == "present":
        sha, size = sha_hint, int(size_hint or 0)
    else:
        sha, size = _fetch(source, sha_hint, size_hint, store, stop, say, name, options)
    if unchanged and sha != recorded_sha:
        return _drifted(report, say, name, recorded_sha, sha, recorded_size, size)
    kind = sniff(store.blob(sha))
    if recorded_sha is not None and recorded_sha != sha:
        report.changed.append({"name": name, "old": recorded_sha, "new": sha})
    row.update(new)
    row.update(size=size, sha256=sha, format=kind)
    say({"event": "pin.file", "file": name, "sha256": sha, "size": size, "format": kind})
    return {"name": name, "sha256": sha, "size": size, "format": kind, "outcome": "pinned"}


def _drifted(
    report: Report, say: Say, name: str, recorded: Any, found: str, recorded_size: Any, size: Any
) -> dict[str, Any]:
    row = {"name": name, "recorded": recorded, "found": found, "recorded_size": recorded_size, "size": size}
    report.drifted.append(row)
    say({"event": "pin.drifted", "file": name, **row})
    return {"name": name, "sha256": recorded, "size": recorded_size, "outcome": "left"}


def _fetch(
    source: str,
    sha_hint: str | None,
    size_hint: int | None,
    store: Store,
    stop: threading.Event,
    say: Say,
    name: str,
    options: dict[str, Any],
) -> tuple[str, int]:
    """Download through the store: the file is placed under its sha256 (or found there already),
    and its partial, keyed by sha256 when the hub names it and by the URL when not, is gone."""
    partial = store.partial(sha_hint) if sha_hint is not None else store.url_partial(source)
    transfer = Transfer(
        source,
        partial,
        size_hint if sha_hint is not None else None,
        sha_hint,
        stop,
        phase=lambda phase, detail: say({"event": "pin.phase", "file": name, "phase": phase, **detail}),
        free_bytes=store.free_bytes,
        **options,
    )
    sha, size = transfer.run()
    if store.presence(sha, size).status == "present":
        store.drop_partial(partial)
    else:
        store.place(partial, sha)
    return sha, size
