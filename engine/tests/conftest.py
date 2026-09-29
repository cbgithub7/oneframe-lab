"""Test nodes are written on the fly into a temporary folder, each a real node.json plus node.py,
so every test goes through the same discovery, validation and execution a real node does."""

from __future__ import annotations

import json
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from oneframe.cache import Cache
from oneframe.registry import Registry, discover
from oneframe.scheduler import Scheduler

MakeNode = Callable[..., Path]

# A node that turns an image into a constant depth plane and a pinhole camera.
CONST_DEPTH = {
    "manifest": {
        "id": "test.const_depth",
        "version": "1",
        "title": "Constant depth",
        "category": "test",
        "inputs": {"image": "Image"},
        "outputs": {
            "depth": {"type": "Depth[kind=metric, measure=z]", "trust": "predicted"},
            "camera": {"type": "Camera[model=pinhole, source=estimated]", "trust": "predicted"},
        },
        "params": {
            "z": {"type": "float", "default": 2.0, "min": 0.1},
            "batch": {"type": "int", "default": 1, "min": 1, "affects": "speed"},
        },
    },
    "code": """
        import json
        import numpy as np

        CALLS = []

        def run(ctx):
            CALLS.append(ctx.params)
            h, w = 4, 6
            path = ctx.path("depth.npz")
            np.savez(path, depth=np.full((h, w), ctx.params["z"], dtype=np.float32))
            ctx.output("depth", path)
            cam = ctx.path("camera.json")
            cam.write_text(json.dumps({"fx": 3.0, "fy": 3.0, "cx": 3.0, "cy": 2.0, "width": w, "height": h}))
            ctx.output("camera", cam)
    """,
}

# A source with no inputs that writes a tiny image; its content depends on a param.
FAKE_IMAGE = {
    "manifest": {
        "id": "test.image",
        "version": "1",
        "title": "Fake image",
        "category": "test",
        "outputs": {"image": {"type": "Image[alpha=none]", "trust": "measured"}},
        "params": {"seed": {"type": "int", "default": 0}},
    },
    "code": """
        def run(ctx):
            p = ctx.path("image.bin")
            p.write_bytes(bytes([ctx.params["seed"] % 256]) * 16)
            ctx.output("image", p)
    """,
}


def _write(root: Path, manifest: dict[str, Any], code: str) -> Path:
    folder = root / manifest["id"]
    folder.mkdir(parents=True, exist_ok=True)
    manifest = dict(manifest)
    manifest.setdefault("run", {"where": "engine", "entry": "node.py:run"})
    (folder / "node.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "node.py").write_text(textwrap.dedent(code), encoding="utf-8")
    return folder


@pytest.fixture
def node_root(tmp_path: Path) -> Path:
    root = tmp_path / "nodes"
    root.mkdir()
    return root


@pytest.fixture
def make_node(node_root: Path) -> MakeNode:
    def make(manifest: dict[str, Any], code: str = "def run(ctx):\n    pass\n") -> Path:
        return _write(node_root, manifest, code)

    return make


@pytest.fixture
def basic_nodes(make_node: MakeNode) -> None:
    make_node(FAKE_IMAGE["manifest"], FAKE_IMAGE["code"])
    make_node(CONST_DEPTH["manifest"], CONST_DEPTH["code"])


@pytest.fixture
def registry_for(node_root: Path) -> Callable[[], Registry]:
    return lambda: discover([node_root])


@pytest.fixture
def scheduler_for(tmp_path: Path, node_root: Path) -> Callable[..., Scheduler]:
    def make(**kw: Any) -> Scheduler:
        kw.setdefault("runtime_python", lambda _runtime: Path(sys.executable))
        return Scheduler(discover([node_root]), Cache(tmp_path / "cache"), log_dir=tmp_path / "logs", **kw)

    return make


TINY_RUNTIME = Path(__file__).parent / "runtimes" / "tiny"
MakeRuntime = Callable[..., Path]


def write_runtime(
    root: Path,
    definition: dict[str, Any],
    lock: str = 'version = 1\nrequires-python = "==3.11.*"\n',
    extras: list[str] | None = None,
    package: bool = False,
) -> Path:
    """A runtime folder for tests that plan or check definitions: runtime.json as given, a
    pyproject with an extra per build (all in one conflict set) and the lock text as given."""
    folder = root / definition["id"]
    folder.mkdir(parents=True, exist_ok=True)
    names = extras if extras is not None else [b["name"] for b in definition.get("builds") or []]
    conflicts = ", ".join(f'{{ extra = "{n}" }}' for n in names)
    pyproject = (
        f'[project]\nname = "rt-{definition["id"]}"\nversion = "1"\nrequires-python = ">=3.11"\n'
        "[project.optional-dependencies]\n"
        + "".join(f"{n} = []\n" for n in names)
        + f"[tool.uv]\npackage = {'true' if package else 'false'}\n"
        + (f"conflicts = [[{conflicts}]]\n" if len(names) > 1 else "")
    )
    (folder / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    (folder / "uv.lock").write_text(lock, encoding="utf-8")
    (folder / "runtime.json").write_text(json.dumps(definition, indent=2), encoding="utf-8")
    return folder


@pytest.fixture
def runtime_root(tmp_path: Path) -> Path:
    root = tmp_path / "runtimes"
    root.mkdir()
    return root


@pytest.fixture
def make_runtime(runtime_root: Path) -> MakeRuntime:
    def make(definition: dict[str, Any], **kw: Any) -> Path:
        return write_runtime(runtime_root, definition, **kw)

    return make


class Events(list[dict[str, Any]]):
    def kinds(self) -> list[str]:
        return [e["event"] for e in self]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [e for e in self if e["event"] == kind]


@pytest.fixture
def events() -> Events:
    return Events()
