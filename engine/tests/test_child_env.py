"""What a runtime child writes stays under the data root (spec 006, task 5, AC2's environment
part): library caches in the runtime's folder under cache/runtime/, temporary files in the job's
own folder, no bytecode; the engine's bytecode under cache/pycache/; the cache tagged for backup
tools."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import threading
from pathlib import Path

import pytest
from conftest import NO_GPU

from oneframe import BUILTIN_NODES_DIR
from oneframe.environment import (
    LIBRARY_CACHES,
    OVERRIDING_CACHES,
    ROOT_ENV,
    child_env,
    reserved_env,
    uv_environment,
)
from oneframe.executors import ProcessExecutor
from oneframe.layout import CACHEDIR_TAG, Layout, claim_root, keep_bytecode_under
from oneframe.manifest import parse
from oneframe.node.job import Job
from oneframe.server import Engine


def test_the_child_environment_puts_every_cache_and_temporary_folder_under_the_root(tmp_path: Path) -> None:
    person = {
        **os.environ,
        "HF_HOME": "/home/ana/.cache/huggingface",
        "XDG_CACHE_HOME": "/home/ana/.cache",
        "TMP": "/tmp",
        "PYTHONPYCACHEPREFIX": str(tmp_path / "engine-pycache"),
    }
    caches, job = tmp_path / "root" / "cache" / "runtime" / "torch", tmp_path / "root" / "cache" / "tmp" / "0"
    env = child_env(person, {"TORCH_HOME": "/elsewhere"}, caches=caches, tmp=job)
    for name, folder in LIBRARY_CACHES.items():
        assert env[name] == str(caches / folder), name
    assert env["TMP"] == env["TEMP"] == env["TMPDIR"] == str(job)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTHONPYCACHEPREFIX" not in env  # the engine's own; the child reads its runtime's
    assert {"HF_HOME", "TORCH_HOME", "TMPDIR", "XDG_CACHE_HOME"} <= set(ROOT_ENV)
    assert all(reserved_env(name.lower()) for name in ROOT_ENV)  # no runtime definition sets them


def test_a_persons_own_hub_and_triton_caches_never_reach_a_child(tmp_path: Path) -> None:
    """Each of these overrides HF_HOME or TRITON_HOME: kept, it would let a run read weights from
    the person's own cache, which Download never fetched, or write outside the root."""
    person = {**os.environ, **{name: f"/home/ana/elsewhere/{name.lower()}" for name in OVERRIDING_CACHES}}
    defined = {"HF_HUB_CACHE": "/runtime/says/so", "transformers_cache": "/runtime/too"}
    env = child_env(person, defined, caches=tmp_path / "caches", tmp=tmp_path / "job")
    named = {"HF_HUB_CACHE", "TRANSFORMERS_CACHE", "HF_MODULES_CACHE", "TRITON_CACHE_DIR"}
    legacy = {"HUGGINGFACE_HUB_CACHE", "HUGGINGFACE_ASSETS_CACHE", "PYTORCH_TRANSFORMERS_CACHE"}
    assert named | legacy <= set(OVERRIDING_CACHES)
    assert not [name for name in env if name.upper() in OVERRIDING_CACHES]
    assert env["HF_HOME"] == str(tmp_path / "caches" / "huggingface")


def test_uv_never_sees_the_engines_bytecode_prefix() -> None:
    env = uv_environment(Path("/root/uv"), base={"PYTHONPYCACHEPREFIX": "/x", "KEEP": "1"})
    assert "PYTHONPYCACHEPREFIX" not in env and env["KEEP"] == "1"


def test_uv_and_runs_drop_the_same_names_in_any_case(tmp_path: Path) -> None:
    """uv and a run start from one inherited environment: neither gets the engine's own Python, its
    folders on PATH, or a hub token, however the person's environment spells the names (spec 008)."""
    engine_bin = Path(sys.prefix) / ("Scripts" if sys.platform == "win32" else "bin")
    person = {
        "CONDA_DEFAULT_ENV": "base",
        "HF_TOKEN": "hf_secret",
        "virtual_env": "/engine/.venv",
        "PythonPath": "/engine/src",
        "UV_PYTHON": "3.15",
        "Path": os.pathsep.join([str(engine_bin), str(tmp_path / "bin")]),
        "KEEP": "1",
    }
    for env in (uv_environment(tmp_path / "uv", base=person), child_env(person, {})):
        assert not {"CONDA_DEFAULT_ENV", "HF_TOKEN", "VIRTUAL_ENV", "PYTHONPATH", "UV_PYTHON"} & {
            name.upper() for name in env
        }
        assert env["Path"] == str(tmp_path / "bin") and env["KEEP"] == "1"


def test_a_child_writes_no_bytecode_and_sees_the_roots_folders(tmp_path: Path) -> None:
    code = tmp_path / "node" / "node.py"
    helper = tmp_path / "node" / "helper.py"
    code.parent.mkdir()
    helper.write_text("VALUE = 1\n", encoding="utf-8")
    names = [*LIBRARY_CACHES, "TMPDIR", "PYTHONDONTWRITEBYTECODE"]
    code.write_text(
        textwrap.dedent(
            f"""
            import json, os, tempfile
            from . import helper

            def run(ctx):
                seen = {{n: os.environ.get(n) for n in {names!r}}}
                seen["tempdir"] = tempfile.gettempdir()
                ctx.stage("env", json.dumps(seen))
            """
        ),
        encoding="utf-8",
    )
    caches, jobs = tmp_path / "root" / "cache" / "runtime" / "tiny", tmp_path / "root" / "cache" / "tmp" / "0"
    jobs.mkdir(parents=True)
    seen: list[dict[str, object]] = []
    job = Job(node="test.env", folder=code.parent, entry="node.py:run", out_dir=tmp_path / "out")
    ProcessExecutor(Path(sys.executable), tmp_root=jobs, caches=caches).execute(
        job, seen.append, threading.Event()
    )
    env = json.loads(str(seen[0]["message"]))
    for name, folder in LIBRARY_CACHES.items():
        assert env[name] == str(caches / folder)
    assert Path(env["TMPDIR"]).parent == jobs and Path(env["tempdir"]) == Path(env["TMPDIR"])
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert not (tmp_path / "node" / "__pycache__").exists()
    assert list(jobs.iterdir()) == []  # the job's folder went with the job


def test_an_engine_gives_each_runtime_node_its_runtimes_cache_folder(tmp_path: Path) -> None:
    engine = Engine(tmp_path, [BUILTIN_NODES_DIR], lambda _m: None, profile=lambda: NO_GPU)
    try:
        engine.runtimes.python_for = lambda _r: Path(sys.executable)  # type: ignore[method-assign]
        engine.runtimes.env_for = lambda _r: {}  # type: ignore[method-assign]
        folder = tmp_path / "node"
        folder.mkdir()
        (folder / "node.py").write_text("def run(ctx):\n    pass\n", encoding="utf-8")
        row = {
            "id": "test.rt",
            "version": "1",
            "title": "t",
            "category": "test",
            "outputs": {"image": {"type": "Image", "trust": "synthetic"}},
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
        }
        manifest = parse(row, folder)
        executor = engine.scheduler.executors.executor(manifest, "cpu")
        assert isinstance(executor, ProcessExecutor)
        assert executor.caches == Layout(tmp_path).runtime_caches / "tiny"
        assert executor.tmp_root == engine.scratch.folder
        # A runtime node's cache key takes the build and marker hashes from the engine's runtimes;
        # the scheduler's default (no runtime part) would serve results from before a reinstall.
        assert engine.scheduler.runtimes is engine.runtimes
    finally:
        engine.shutdown()


def test_the_cache_is_tagged_for_backup_tools(tmp_path: Path) -> None:
    claim_root(tmp_path, packaged=False)
    tag = Layout(tmp_path).cachedir_tag
    assert tag.read_text(encoding="utf-8") == CACHEDIR_TAG
    assert tag.read_text(encoding="utf-8").startswith("Signature: 8a477f597d28d172789f06886806bc55")


def test_the_engines_bytecode_goes_under_the_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "pycache_prefix", None)
    keep_bytecode_under(tmp_path)
    assert sys.pycache_prefix == str(Layout(tmp_path).pycache)
