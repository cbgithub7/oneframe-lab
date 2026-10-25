"""Hashes kept on disk keep their values (spec 011, AC1). Each case works one out from fixed bytes
and compares it with the value the code gave before spec 011 moved the hashing: a node's code
digest and a cache key decide which results are served, a runtime's hashes whether its marker is
out of date, and a pin's partial name which download resumes. A new value here would run every
cached result again, make every runtime out of date, or start a pin's download again."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from oneframe import runtimes
from oneframe.cache import Cache, code_digest
from oneframe.manifest import parse
from oneframe.models.store import Store

NODE = {
    "id": "test.kept",
    "version": "1",
    "title": "Kept",
    "category": "test",
    "outputs": {"text": "Text"},
    "params": {"path": {"type": "file"}, "n": {"type": "int", "default": 1}},
}
RUNTIME = {
    "id": "kept",
    "title": "Kept",
    "summary": "A runtime whose hashes are recorded.",
    "python": "3.11",
    "builds": [{"name": "cpu", "vendor": "none", "disk_mb": 1}],
    "extensions": [
        {"package": "fast", "class": "stand-in", "for": "a compiled helper", "stand_in": "stand-ins/fast"}
    ],
    "sources": [],
    "env": {},
}


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _node_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "node"
    _write(folder / "node.json", json.dumps(NODE, indent=2).encode())
    _write(folder / "node.py", b"def run(ctx):\n    pass\n")
    _write(folder / "helper.py", b"X = 1\r\n")
    _write(folder / "data" / "table.bin", bytes(range(256)) * 5000)  # more than one chunk
    _write(folder / "__pycache__" / "node.cpython-315.pyc", b"\x00compiled")
    _write(folder / ".node.json.0123456789ab.tmp", b"a pin killed outright")
    return folder


def _code(tmp_path: Path) -> str:
    return code_digest(_node_folder(tmp_path))


def _cache_key(tmp_path: Path) -> str:
    folder = _node_folder(tmp_path)
    picked = _write(tmp_path / "photo.bin", b"\x89PNG not really" * 100_000)
    manifest = parse(dict(NODE), folder)
    return Cache(tmp_path / "cache" / "objects", tmp_path / "cache" / "tmp").key(
        manifest, {"path": str(picked), "n": 3}, {}
    )


def _runtime(tmp_path: Path) -> dict[str, str]:
    folder = tmp_path / "runtimes" / "kept"
    _write(folder / "runtime.json", json.dumps(RUNTIME, indent=2).replace("\n", "\r\n").encode())
    _write(folder / "uv.lock", b'version = 1\r\nrequires-python = "==3.11.*"\r\n')
    _write(
        folder / "pyproject.toml",
        b'[project]\nname = "rt-kept"\nversion = "1"\nrequires-python = ">=3.11"\n'
        b"[project.optional-dependencies]\ncpu = []\n[tool.uv]\npackage = false\n",
    )
    _write(folder / "stand-ins" / "fast" / "__init__.py", b"KIND = 'stand-in'\r\n")
    _write(folder / "stand-ins" / "fast" / "inner" / "more.py", b"Y = 2\n")
    _write(folder / "stand-ins" / "fast" / "__pycache__" / "x.pyc", b"\x00")
    return runtimes.load(folder / "runtime.json").hashes()


def _partial(tmp_path: Path) -> str:
    url = "https://huggingface.co/org/model/resolve/0123abc/weights/model.safetensors?download=true"
    return Store(tmp_path / "data").url_partial(url).name


RECORDED: dict[str, tuple[Callable[[Path], object], object]] = {
    "code": (_code, "1430899a639e119ffa2bf557dca0c698622b49b29d6e320d7de3e5bf68a9edc4"),
    "cache_key": (_cache_key, "d502843ff2295d6026ec65900ca7c69d5e1e6d9e2c34cbda760b641c77ebaca6"),
    "runtime": (
        _runtime,
        {
            "lock_sha256": "0663ceedaa46525a18c19abf7a94d5c3d128ebe50cb43945bd661066982bbc65",
            "definition_sha256": "3e52669b36d86b86968497f3126119f1036e7ba3e928eeb1b1a5916a58973014",
            "stand_ins_sha256": "951a130ca5edc479d359d55ec43ae71b184d7af67d8d141e6f315d8aeed2f0af",
        },
    ),
    "partial": (_partial, "url-1984f9b3b6e64015e812b552e81671ce1a2520c4c9efc26b4b3ea5c5a98f8403"),
}


@pytest.mark.parametrize("case", list(RECORDED))
def test_recorded_hashes_keep_their_values(tmp_path: Path, case: str) -> None:
    work, recorded = RECORDED[case]
    assert work(tmp_path) == recorded
