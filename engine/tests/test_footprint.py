"""Nothing is written outside the data root (AC2 of spec 006).

The real engine runs as the app runs it, with every folder a program might write to by default
(HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG_*, TMP, TEMP, TMPDIR) pointed into a sandbox, and no
--data, so it finds its own root there. It installs the tiny runtime (uv fetches its Python into the
new root), runs an engine node and a runtime node, and stops. Afterwards nothing new exists in the
sandbox outside the root; on Windows the real profile's top-level folders and the registry keys
uv would touch are unchanged. The runtime node reports its environment, which must put every
variable spec 006's Design lists under the root."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import TINY_RUNTIME

from oneframe.executors import LIBRARY_CACHES
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
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(__file__))
import helper

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


def _snapshot(folder: Path) -> set[str]:
    return {str(p.relative_to(folder)) for p in folder.rglob("*")}


def _top_level(folders: list[str | None]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for folder in folders:
        if folder and Path(folder).is_dir():
            out[folder] = {p.name for p in Path(folder).iterdir()}
    return out


def _registry() -> dict[str, Any]:
    """HKCU\\Software\\Python, every key and value below it, and HKCU\\Environment's Path: what uv
    changes when it registers a Python or puts its bin folder on the path."""
    found: dict[str, Any] = {}
    if sys.platform != "win32":
        return found
    import winreg

    def walk(path: str) -> None:
        try:
            handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with handle:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                found[f"{path}:{name}"] = value
                index += 1
            index = 0
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(handle, index))
                except OSError:
                    break
                index += 1
        found[path] = sorted(children)
        for child in children:
            walk(f"{path}\\{child}")

    walk(r"Software\Python")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            found["Environment:Path"] = winreg.QueryValueEx(handle, "Path")[0]
    except OSError:
        found["Environment:Path"] = None
    return found


class _Engine:
    def __init__(self, argv: list[str], env: dict[str, str], cwd: Path, log: Path):
        self.log = log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            env=env,
            cwd=cwd,
            text=True,
            encoding="utf-8",
        )
        self.lines: queue.Queue[dict[str, Any]] = queue.Queue()
        self.seen: list[dict[str, Any]] = []
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            if line.strip():
                self.lines.put(json.loads(line))

    def wait(self, match: Callable[[dict[str, Any]], bool], what: str) -> dict[str, Any]:
        end = time.monotonic() + WAIT_S
        while time.monotonic() < end:
            try:
                message = self.lines.get(timeout=0.5)
            except queue.Empty:
                continue
            self.seen.append(message)
            if match(message):
                return message
        raise TimeoutError(f"{what} did not arrive; the engine's log is {self.log.name}")

    def call(self, ident: int, method: str, params: dict[str, Any]) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.proc.stdin.write(json.dumps({"id": ident, "method": method, "params": params}) + "\n")
        self.proc.stdin.flush()
        return self.wait(lambda m: m.get("id") == ident, method)

    def close(self) -> int:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        code = self.proc.wait(timeout=60)
        self.log.close()
        return code


def test_ac2_nothing_is_written_outside_the_root(tmp_path: Path, uv_exe: str) -> None:
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
    env = {k: v for k, v in os.environ.items() if k.upper() not in ("ONEFRAME_DATA", "HOMEDRIVE", "HOMEPATH")}
    env.update({name: str(folder) for name, folder in env_folders.items()})
    root = Path(data_root(env, sys.platform, str(home), packaged=False))
    env["PYTHONPYCACHEPREFIX"] = str(root / "cache" / "pycache")  # as the app starts it

    nodes = sandbox / "nodes"
    _write_node(nodes, ENGINE_NODE)
    _write_node(nodes, RUNTIME_NODE)
    runtimes = sandbox / "runtimes"
    shutil.copytree(TINY_RUNTIME, runtimes / "tiny")

    before = _snapshot(sandbox)
    real = [os.environ.get(n) for n in ("USERPROFILE", "APPDATA", "LOCALAPPDATA", "HOME")]
    real_before, registry_before = _top_level(real), _registry()

    argv = [sys.executable, "-m", "oneframe.server", "--nodes", str(nodes), "--runtimes", str(runtimes)]
    engine = _Engine([*argv, "--uv", uv_exe], env, sandbox, tmp_path / "engine.log")
    try:
        ready = engine.wait(lambda m: m.get("event") == "engine.ready", "engine.ready")
        assert Path(ready["data"]) == root
        reply = engine.call(1, "runtimes.install", {"runtime": "tiny"})
        assert "error" not in reply, reply
        done = engine.wait(lambda m: m.get("event") in ("runtime.done", "runtime.failed"), "the install")
        assert done["event"] == "runtime.done", done
        graph = {
            "version": 1,
            "nodes": {"e": {"node": ENGINE_NODE["id"]}, "r": {"node": RUNTIME_NODE["id"]}},
            "edges": [],
        }
        reply = engine.call(2, "graph.run", {"graph": graph})
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

    # Nothing new in the sandbox outside the root.
    root_part = str(root.relative_to(sandbox))
    outside = sorted(
        p for p in _snapshot(sandbox) - before if p != root_part and not p.startswith(root_part + os.sep)
    )
    assert outside == [], outside
    assert (root / "uv" / "python").is_dir() and any((root / "uv" / "python").iterdir())
    assert not (root / "cache" / "tmp").exists() or list((root / "cache" / "tmp").iterdir()) == []

    # The real profile, and the registry, are as they were.
    after = _top_level(real)
    for folder, names in real_before.items():
        assert after.get(folder, set()) - names == set(), folder
    assert _registry() == registry_before
