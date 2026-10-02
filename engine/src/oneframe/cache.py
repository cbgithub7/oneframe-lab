"""Every node output, stored by what produced it.

A node's key is a hash of: the node's id and version, its output-affecting parameters (a file
parameter by the file's content, not its name), and the keys of the values it was given. The same
node with the same inputs and settings therefore has the same key on any run, from any recipe --
so changing one node re-runs only what comes after it, and two recipes that start the same way
share the start.

Writes are atomic: a run writes into a private folder and renames it into place, so a crash or a
Stop never leaves half an output that a later run would trust.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import shutil
import uuid
from pathlib import Path
from typing import Any

from oneframe.child import Value
from oneframe.errors import ContractError
from oneframe.manifest import Manifest

KEY_VERSION = 1


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class Cache:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.objects = self.root / "objects"
        self.tmp = self.root / "tmp"
        self._digests: dict[tuple[str, int, int], str] = {}

    def _file_key(self, value: str) -> str:
        path = Path(value)
        stat = path.stat()
        memo = (str(path.resolve()), stat.st_size, stat.st_mtime_ns)
        if memo not in self._digests:
            self._digests[memo] = file_digest(path)
        return self._digests[memo]

    def key(self, manifest: Manifest, params: dict[str, Any], inputs: dict[str, Value]) -> str:
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

    def clear_tmp(self) -> None:
        with contextlib.suppress(OSError):
            shutil.rmtree(self.tmp)
