"""Nothing is written outside the data root (AC2 of spec 006).

The real engine runs as the app runs it, with every folder a program might write to by default
(HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG_*, TMP, TEMP, TMPDIR) pointed into a sandbox, and no
--data, so it finds its own root there. It installs the tiny runtime (uv fetches its Python into the
new root), runs an engine node and a runtime node, and stops. Afterwards nothing new exists in the
sandbox outside the root; in the real profile nothing is new or changed under the default folders
of what the engine's tree runs (footprint.WATCHED), and on Windows the registry keys uv would touch
are unchanged. The runtime node reports its environment, which must put every
variable spec 006's Design lists under the root.

A write outside the root passes only where an entry of `outside` in contracts/layout.json allows
it for what the engine's process tree runs. Each check is a function in fixtures/footprint.py, and
the tests after this one show each failing."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from conftest import TINY_RUNTIME, StartEngine
from fixtures import footprint

from oneframe import REPO_ROOT
from oneframe.environment import LIBRARY_CACHES
from oneframe.layout import data_root

WAIT_S = 300  # uv fetches Python 3.11 into the new root

ENGINE_NODE = {
    "id": "test.footprint_engine",
    "version": "1",
    "title": "Runs in the engine",
    "category": "test",
    "outputs": {"text": "Text"},
    "run": {"where": "engine", "entry": "node.py:run"},
}
RUNTIME_NODE = {
    "id": "test.footprint_runtime",
    "version": "1",
    "title": "Runs in the tiny runtime",
    "category": "test",
    "outputs": {"text": "Text"},
    "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
}
# The runtime node imports a module beside it, so a child that wrote bytecode would leave it in
# the node's folder, outside the root; it also makes a temporary file the usual way.
CODE = """
import json, os, tempfile
from . import helper

NAMES = {names!r}

def run(ctx):
    handle, name = tempfile.mkstemp()
    os.close(handle)
    seen = {{n: os.environ.get(n) for n in NAMES}}
    seen["tempfile"] = name
    ctx.stage("env", json.dumps(seen))
    path = ctx.path("seen.txt")
    path.write_text(helper.WORD, encoding="utf-8")
    ctx.output("text", path)
"""


def _write_node(root: Path, manifest: dict[str, Any]) -> None:
    folder = root / manifest["id"]
    folder.mkdir(parents=True)
    (folder / "node.json").write_text(json.dumps(manifest), encoding="utf-8")
    names = [*LIBRARY_CACHES, "TMP", "TEMP", "TMPDIR", "PYTHONDONTWRITEBYTECODE"]
    (folder / "node.py").write_text(CODE.format(names=names), encoding="utf-8")
    (folder / "helper.py").write_text('WORD = "here"\n', encoding="utf-8")


def test_ac2_nothing_is_written_outside_the_root(
    tmp_path: Path, uv_exe: str, engine_process: StartEngine
) -> None:
    sandbox = tmp_path / "sandbox"
    home = sandbox / "home"
    env_folders = {
        "HOME": home,
        "USERPROFILE": home,
        "APPDATA": home / "AppData" / "Roaming",
        "LOCALAPPDATA": home / "AppData" / "Local",
        "XDG_DATA_HOME": sandbox / "xdg" / "data",
        "XDG_CONFIG_HOME": sandbox / "xdg" / "config",
        "XDG_CACHE_HOME": sandbox / "xdg" / "cache",
        "XDG_STATE_HOME": sandbox / "xdg" / "state",
        "XDG_RUNTIME_DIR": sandbox / "xdg" / "runtime",
        "TMP": sandbox / "tmp",
        "TEMP": sandbox / "tmp",
        "TMPDIR": sandbox / "tmp",
    }
    for folder in env_folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    # Nothing of the outer uv's own setup (CI's setup-uv exports UV_CACHE_DIR, for one) may stand in
    # for what the engine sets: each would hide a setting the engine forgot.
    dropped = ("ONEFRAME_DATA", "HOMEDRIVE", "HOMEPATH")
    env = {
        k: v for k, v in os.environ.items() if k.upper() not in dropped and not k.upper().startswith("UV_")
    }
    env.update({name: str(folder) for name, folder in env_folders.items()})
    root = Path(data_root(env, sys.platform, str(home), packaged=False))
    env["PYTHONPYCACHEPREFIX"] = str(root / "cache" / "pycache")  # as the app starts it

    nodes = sandbox / "nodes"
    _write_node(nodes, ENGINE_NODE)
    _write_node(nodes, RUNTIME_NODE)
    runtimes = sandbox / "runtimes"
    shutil.copytree(TINY_RUNTIME, runtimes / "tiny")

    before = footprint.snapshot(sandbox)
    real = footprint.watched(footprint.WATCHED, os.environ, footprint.system())
    real_before, registry_before = footprint.profile(real), footprint.registry()

    argv = [sys.executable, "-m", "oneframe.server", "--nodes", str(nodes), "--runtimes", str(runtimes)]
    engine = engine_process([*argv, "--uv", uv_exe], env=env, cwd=sandbox, wait_s=WAIT_S)
    try:
        ready = engine.wait(lambda m: m.get("event") == "engine.ready", "engine.ready")
        assert Path(ready["data"]) == root
        reply = engine.call("runtimes.install", {"runtime": "tiny"})
        assert "error" not in reply, reply
        done = engine.wait(lambda m: m.get("event") in ("runtime.done", "runtime.failed"), "the install")
        assert done["event"] == "runtime.done", done
        graph = {
            "version": 1,
            "nodes": {"e": {"node": ENGINE_NODE["id"]}, "r": {"node": RUNTIME_NODE["id"]}},
            "edges": [],
        }
        reply = engine.call("graph.run", {"graph": graph})
        assert "error" not in reply, reply
        end = engine.wait(lambda m: m.get("event") in ("run.done", "run.failed", "run.stopped"), "the run")
        assert end["event"] == "run.done", end
    finally:
        assert engine.close() == 0

    # The runtime node's environment put every library cache and temporary folder under the root.
    [runtime_seen] = [
        json.loads(m["message"]) for m in engine.seen if m.get("event") == "stage" and m["step"] == "r"
    ]
    caches = root / "cache" / "runtime" / "tiny"
    for name, folder in LIBRARY_CACHES.items():
        assert Path(runtime_seen[name]) == caches / folder, name
    for name in ("TMP", "TEMP", "TMPDIR", "tempfile"):
        assert Path(runtime_seen[name]).is_relative_to(root / "cache" / "tmp"), name
    assert runtime_seen["PYTHONDONTWRITEBYTECODE"] == "1"

    # Nothing new in the sandbox outside the root, but where an exception allows it.
    allowed = footprint.allowed(footprint.OUTSIDE, footprint.ENGINE_TREE, env, REPO_ROOT, footprint.system())
    outside = footprint.written_outside(before, footprint.snapshot(sandbox), sandbox, root, allowed)
    assert outside == [], outside
    assert (root / "uv" / "python").is_dir() and any((root / "uv" / "python").iterdir())
    assert (root / "uv" / "cache").is_dir() and any((root / "uv" / "cache").iterdir())
    assert not (root / "cache" / "tmp").exists() or list((root / "cache" / "tmp").iterdir()) == []

    # The real profile, and the registry, are as they were.
    assert footprint.profile_changes(real_before, footprint.profile(real)) == []
    assert footprint.registry_changes(registry_before, footprint.registry()) == []


def test_a_write_an_exception_names_passes_only_while_it_is_listed(tmp_path: Path) -> None:
    """AC1: a write where an entry allows the engine is not reported; once the entry is gone, or names
    the app as its writer, it is."""
    sandbox = tmp_path / "sandbox"
    root, home = sandbox / "root", sandbox / "home"
    root.mkdir(parents=True)
    home.mkdir()
    env = {"HOME": str(home)}
    entry = {
        "writer": "engine",
        "systems": [footprint.system()],
        "what": "-",
        "why": "-",
        "paths": ["{HOME}/.cache/thing"],
    }
    before = footprint.snapshot(sandbox)
    (home / ".cache" / "thing").mkdir(parents=True)
    (home / ".cache" / "thing" / "kept.bin").write_bytes(b"x")
    (root / "inside.txt").write_text("x", encoding="utf-8")
    after = footprint.snapshot(sandbox)

    def reported(entries: list[dict[str, Any]]) -> list[str]:
        allowed = footprint.allowed(entries, footprint.ENGINE_TREE, env, tmp_path, footprint.system())
        return footprint.written_outside(before, after, sandbox, root, allowed)

    assert reported([entry]) == []
    made = [(".cache",), (".cache", "thing"), (".cache", "thing", "kept.bin")]
    written = [str(Path("home", *parts)) for parts in made]
    assert reported([]) == written
    assert reported([{**entry, "writer": "app"}]) == written
    assert reported([{**entry, "systems": ["elsewhere"]}]) == written


def test_every_exception_says_who_writes_it_where_and_why() -> None:
    assert footprint.OUTSIDE, "contracts/layout.json lists the data root's exceptions"
    every = {name: f"/{name}" for name in footprint.VARIABLES}
    for entry in footprint.OUTSIDE:
        assert set(entry) == {"writer", "systems", "what", "why", "paths"}, entry
        assert entry["writer"] in footprint.WRITERS, entry
        assert entry["systems"] and set(entry["systems"]) <= set(footprint.SYSTEMS), entry
        assert entry["what"].strip() and entry["why"].strip(), entry
        for spelled in entry["paths"]:  # from the environment or the checkout, nothing else
            assert footprint.expand(spelled, every, Path("/checkout")) is not None, spelled


def test_a_registry_change_is_reported() -> None:
    before = {"Software\\Python": ["PythonCore"], "Environment:Path": "C:\\bin"}
    assert footprint.registry_changes(before, dict(before)) == []
    changed = {**before, "Environment:Path": "C:\\bin;C:\\uv", "Software\\Python\\Astral": []}
    assert footprint.registry_changes(before, changed) == ["Environment:Path", "Software\\Python\\Astral"]
    assert footprint.registry_changes(changed, before) == ["Environment:Path", "Software\\Python\\Astral"]


def _profile(home: Path) -> tuple[list[Path], footprint.Profile]:
    """A profile with one watched folder that exists, holding a cache folder, and one that does not."""
    (home / "AppData" / "uv" / "cache").mkdir(parents=True)
    (home / "AppData" / "uv" / "cache" / "old.whl").write_bytes(b"old")
    folders = [home / "AppData" / "uv", home / ".local" / "bin"]
    return folders, footprint.profile(folders)


def test_a_write_inside_a_watched_folder_that_exists_is_seen(tmp_path: Path) -> None:
    """AC2: a file two levels inside a watched folder that existed before; a check of the profile's
    top-level names alone, as before, did not see it."""
    folders, before = _profile(tmp_path)
    made = tmp_path / "AppData" / "uv" / "cache" / "new.whl"
    made.write_bytes(b"new")
    assert str(made) in footprint.profile_changes(before, footprint.profile(folders))


def test_a_new_folder_outside_the_watched_list_is_not_reported(tmp_path: Path) -> None:
    """AC3: a new top-level folder in the profile, as a runner's Firefox once made, is another
    program's; the check of top-level names reported it."""
    folders, before = _profile(tmp_path)
    (tmp_path / "AppData" / "Mozilla" / "Firefox").mkdir(parents=True)
    (tmp_path / "AppData" / "Mozilla" / "Firefox" / "profiles.ini").write_text(
        "[General]\n", encoding="utf-8"
    )
    assert footprint.profile_changes(before, footprint.profile(folders)) == []


def test_a_file_rewritten_in_a_watched_folder_is_seen(tmp_path: Path) -> None:
    folders, before = _profile(tmp_path)
    rewritten = tmp_path / "AppData" / "uv" / "cache" / "old.whl"
    rewritten.write_bytes(b"rewritten, longer")
    assert str(rewritten) in footprint.profile_changes(before, footprint.profile(folders))


def test_a_watched_folder_that_appears_is_seen(tmp_path: Path) -> None:
    folders, before = _profile(tmp_path)
    (tmp_path / ".local" / "bin").mkdir(parents=True)
    assert footprint.profile_changes(before, footprint.profile(folders)) == [str(tmp_path / ".local" / "bin")]
