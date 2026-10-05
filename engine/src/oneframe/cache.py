"""Every node output, stored by what produced it.

A node's key is a hash of: the node's id and version, its output-affecting parameters (a file
parameter by the file's content, not its name), the keys of the values it was given, the code in
its folder (every file but bytecode), and for a node that runs in a runtime, the build this machine
runs and what that build was installed from (the hashes its marker records: lock, definition and
stand-ins). The same node with the same inputs, settings, code and runtime therefore has the same
key on any run, from any recipe -- so changing one node re-runs only what comes after it, two
recipes that start the same way share the start, and a changed node or runtime is never served a
result from before the change.

Writes are atomic: a run writes into a private folder and renames it into place, so a crash or a
Stop never leaves half an output that a later run would trust. The private folders live in the
process's own folder under `cache/tmp/` (scratch.py), on the cache's volume, so the rename is one
step, and a process that dies leaves them to the next start's sweep.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from typing import Any

from oneframe.child import Value
from oneframe.errors import ContractError
from oneframe.manifest import Manifest

# 2: the node's code and its runtime joined the key (spec 006). Older entries are never served;
# they stay on disk until a later spec evicts them.
KEY_VERSION = 2
BYTECODE = (".pyc", ".pyo")


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def code_digest(folder: Path) -> str:
    """A hash of every file in a node's folder, by its path in the folder and its bytes; bytecode
    (`__pycache__/`, `.pyc`) is left out, since it follows from the source and differs by Python.
    Read from the files every time: a size and a time can stay the same when the bytes do not."""
    digest = hashlib.sha256()
    for relative, path in sorted(_code_files(folder)):
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(file_digest(path).encode("ascii"))
    return digest.hexdigest()


def _code_files(folder: Path) -> list[tuple[str, Path]]:
    """(path in the folder, file) for every file but bytecode, following linked folders (code shared
    between nodes, say) but never into one already seen, so a loop of links ends."""
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for here, dirs, files in os.walk(folder, followlinks=True):
        real = os.path.realpath(here)
        if real in seen:
            dirs[:] = []
            continue
        seen.add(real)
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for name in files:
            path = Path(here) / name
            if path.suffix not in BYTECODE and path.is_file():
                out.append((path.relative_to(folder).as_posix(), path))
    return out


class Cache:
    def __init__(self, root: Path, work: Path | None = None):
        self.root = Path(root)
        self.objects = self.root / "objects"
        # Where runs write before their folder is moved into place: the engine gives its own
        # folder under cache/tmp/; a cache made on its own (a test) writes in <root>/tmp.
        self.tmp = Path(work) if work is not None else self.root / "tmp"
        self._digests: dict[tuple[str, int, int], str] = {}

    def _file_key(self, value: str) -> str:
        path = Path(value)
        stat = path.stat()
        memo = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        if memo not in self._digests:
            self._digests[memo] = file_digest(path)
        return self._digests[memo]

    def key(
        self,
        manifest: Manifest,
        params: dict[str, Any],
        inputs: dict[str, Value],
        runtime: dict[str, str] | None = None,
        code: str | None = None,
    ) -> str:
        """`runtime`: for a runtime node, the build this machine runs and its marker's hashes.
        `code`: the node folder's code_digest when the caller has it (once per run), else read now."""
        affecting: dict[str, Any] = {}
        for name, value in sorted(params.items()):
            param = manifest.params.get(name)
            if param is None or param.affects != "output":
                continue
            affecting[name] = {"sha256": self._file_key(value)} if param.type == "file" and value else value
        body = {
            "v": KEY_VERSION,
            "node": manifest.id,
            "version": manifest.version,
            "params": affecting,
            "inputs": {port: value.key for port, value in sorted(inputs.items())},
            "code": code if code is not None else code_digest(manifest.folder),
            "runtime": runtime,
        }
        text = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def folder(self, key: str) -> Path:
        return self.objects / key[:2] / key

    def get(self, key: str) -> dict[str, Value] | None:
        folder = self.folder(key)
        record = folder / "outputs.json"
        if not record.is_file():
            return None
        try:
            rows = json.loads(record.read_text(encoding="utf-8"))
        except OSError, json.JSONDecodeError:
            return None
        out: dict[str, Value] = {}
        for port, row in rows["outputs"].items():
            value = Value.from_json(dict(row, path=str(folder / row["path"])))
            if not value.path.exists():
                return None  # something was removed by hand: treat as absent, never as present
            out[port] = value
        return out

    def begin(self, key: str) -> Path:
        work = self.tmp / f"{key[:16]}-{uuid.uuid4().hex[:8]}"
        work.mkdir(parents=True, exist_ok=False)
        return work

    def commit(
        self, key: str, work: Path, outputs: dict[str, Value], record: dict[str, Any]
    ) -> dict[str, Value]:
        """Move a finished run's folder into place and return its values with their final paths."""
        rows = {}
        for port, value in outputs.items():
            try:
                relative = value.path.resolve().relative_to(work.resolve())
            except ValueError as exc:
                raise ContractError(f"output {port} is outside the run folder: {value.path}") from exc
            rows[port] = dict(value.to_json(), path=relative.as_posix())
        (work / "outputs.json").write_text(
            json.dumps({"key": key, "outputs": rows, "record": record}, indent=2, default=str),
            encoding="utf-8",
        )
        final = self.folder(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            # Another run finished the same work first; its copy is as good as this one.
            shutil.rmtree(work, ignore_errors=True)
        else:
            try:
                work.replace(final)
            except OSError:
                if not final.exists():
                    raise
                shutil.rmtree(work, ignore_errors=True)
        got = self.get(key)
        if got is None:
            raise RuntimeError(f"cache entry {key} vanished after it was written")
        return got

    def abandon(self, work: Path) -> None:
        shutil.rmtree(work, ignore_errors=True)
