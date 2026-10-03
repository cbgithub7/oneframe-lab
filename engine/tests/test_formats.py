"""Every kept file says its format, and nothing newer is overwritten (AC4 of spec 006): the root
file, settings, learned memory (test_files.py) and the runtime marker, and a runtime moved to
another root."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import NO_GPU

from oneframe import BUILTIN_NODES_DIR, runtime_install
from oneframe.layout import LAYOUT_FORMAT, ROOT_FILE, RootRefused, claim_root
from oneframe.memory import read_settings
from oneframe.runtimes import InstallRefused, RuntimeMissing, Runtimes
from oneframe.server import Engine


def _engine(data: Path, **kw: Any) -> Engine:
    return Engine(data, [BUILTIN_NODES_DIR], lambda _m: None, profile=lambda: NO_GPU, **kw)


# -- the root file -------------------------------------------------------------------------------


def test_a_root_whose_layout_is_newer_stops_the_engine_with_a_message(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / ROOT_FILE).write_text('{"format": 99, "kind": "dev"}', encoding="utf-8")
    before = (root / ROOT_FILE).read_bytes()
    done = subprocess.run(
        [sys.executable, "-m", "oneframe.server", "--data", str(root), "--nodes", str(BUILTIN_NODES_DIR)],
        input="",
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert done.returncode == 2
    event = json.loads(done.stdout.splitlines()[0])
    assert event["event"] == "engine.failed" and event["reason"] == "newer_layout"
    assert "newer version" in event["message"] and str(root) in event["message"]
    assert (root / ROOT_FILE).read_bytes() == before
    assert [p.name for p in root.iterdir()] == [ROOT_FILE]  # nothing else was written


def test_a_root_without_a_root_file_gets_one_on_first_start(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "cache").mkdir(parents=True)  # a root from before spec 006: format 0
    engine = _engine(root)
    written = json.loads((root / ROOT_FILE).read_text(encoding="utf-8"))
    assert written["format"] == LAYOUT_FORMAT and written["kind"] == "dev"
    assert engine.hello({})["warnings"] == []


def test_a_root_of_the_other_kind_is_used_with_a_warning(tmp_path: Path) -> None:
    root = tmp_path / "root"
    _engine(root)  # a dev checkout made it
    before = (root / ROOT_FILE).read_bytes()
    warnings = _engine(root, packaged=True).hello({})["warnings"]
    assert len(warnings) == 1 and "dev app" in warnings[0]
    assert (root / ROOT_FILE).read_bytes() == before


def test_a_root_file_that_cannot_be_read_is_left_and_refused(tmp_path: Path) -> None:
    (tmp_path / ROOT_FILE).write_text("{half", encoding="utf-8")
    with pytest.raises(RootRefused) as caught:
        claim_root(tmp_path, packaged=False)
    assert caught.value.reason == "unreadable"
    (tmp_path / ROOT_FILE).write_text('{"format": "one"}', encoding="utf-8")
    with pytest.raises(RootRefused):
        claim_root(tmp_path, packaged=False)
    assert (tmp_path / ROOT_FILE).read_text(encoding="utf-8") == '{"format": "one"}'


# -- settings ------------------------------------------------------------------------------------


def test_settings_without_a_format_are_format_1(tmp_path: Path) -> None:
    (tmp_path / "settings.json").write_text('{"memory": {"fit": "off"}}', encoding="utf-8")
    assert read_settings(tmp_path).fit == "off"
    (tmp_path / "settings.json").write_text('{"format": 1, "memory": {"fit": "off"}}', encoding="utf-8")
    assert read_settings(tmp_path).fit == "off"


def test_newer_settings_are_read_as_the_defaults_with_a_note(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text('{"format": 2, "memory": {"fit": "off"}}', encoding="utf-8")
    settings = read_settings(tmp_path)
    assert settings.fit == "on"
    assert len(settings.notes) == 1 and "format 2, newer" in settings.notes[0]
    path.write_text('{"format": "2", "memory": {"fit": "off"}}', encoding="utf-8")
    assert read_settings(tmp_path).fit == "on" and "format" in read_settings(tmp_path).notes[0]


# -- runtime markers -----------------------------------------------------------------------------


def _fake_install(manager: Runtimes, data: Path, build: str = "cpu", **marker: Any) -> Path:
    """An environment as an install leaves it, without running uv: an interpreter and a marker."""
    env = runtime_install.env_dir(data, "tiny", build)
    python = runtime_install.interpreter(env)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    row = {
        "format": runtime_install.MARKER_FORMAT,
        "runtime": "tiny",
        "build": build,
        "env": str(env.resolve()),
        **manager.get("tiny").hashes(),
        **marker,
    }
    (env / runtime_install.MARKER).write_text(json.dumps(row), encoding="utf-8")
    return env


def _manager(data: Path, tiny: Path) -> Runtimes:
    return Runtimes(data, [tiny.parent], uv=sys.executable, profile=lambda: NO_GPU)


def test_a_marker_records_installed(tmp_path: Path, tiny: Path) -> None:
    manager = _manager(tmp_path / "data", tiny)
    _fake_install(manager, tmp_path / "data")
    assert manager.status(manager.get("tiny"))["status"] == "installed"


def test_a_newer_marker_is_left_byte_for_byte_and_reads_newer_format(tmp_path: Path, tiny: Path) -> None:
    data = tmp_path / "data"
    manager = _manager(data, tiny)
    env = _fake_install(manager, data, format=99, shape="unknown to this app")
    before = (env / runtime_install.MARKER).read_bytes()
    assert manager.status(manager.get("tiny"))["status"] == "newer_format"
    with pytest.raises(RuntimeMissing) as caught:
        manager.python_for("tiny")
    assert caught.value.reason == "newer_format"
    with pytest.raises(InstallRefused, match="newer version"):
        manager.begin_install("tiny")
    with pytest.raises(InstallRefused, match="newer version"):
        manager.remove("tiny")
    with pytest.raises(runtime_install.InstallFailed, match="newer version"):
        runtime_install.install(manager.get("tiny"), "cpu", data, sys.executable, lambda _e: None)
    assert (env / runtime_install.MARKER).read_bytes() == before


def test_a_marker_from_before_formats_is_format_1(tmp_path: Path, tiny: Path) -> None:
    data = tmp_path / "data"
    manager = _manager(data, tiny)
    env = _fake_install(manager, data)
    row = json.loads((env / runtime_install.MARKER).read_text(encoding="utf-8"))
    del row["format"], row["env"]
    (env / runtime_install.MARKER).write_text(json.dumps(row), encoding="utf-8")
    assert manager.status(manager.get("tiny"))["status"] == "installed"


def test_an_environment_moved_to_another_root_reads_as_moved(tmp_path: Path, tiny: Path) -> None:
    first = tmp_path / "first"
    _fake_install(_manager(first, tiny), first)
    second = tmp_path / "second"
    shutil.copytree(first, second, symlinks=True)
    manager = _manager(second, tiny)
    assert manager.status(manager.get("tiny"))["status"] == "moved"
    with pytest.raises(RuntimeMissing) as caught:
        manager.python_for("tiny")
    assert caught.value.reason == "moved" and "install it again" in str(caught.value)
    # The first root's own copy is still where it was built.
    assert _manager(first, tiny).status(_manager(first, tiny).get("tiny"))["status"] == "installed"


def test_the_same_place_spelled_differently_is_not_moved(tmp_path: Path, tiny: Path) -> None:
    data = tmp_path / "data"
    manager = _manager(data, tiny)
    env = runtime_install.env_dir(data, "tiny", "cpu")
    spelled = str(env / ".." / "cpu")
    if sys.platform == "win32":
        spelled = spelled.upper()  # Windows ignores case
    _fake_install(manager, data, env=spelled)
    assert manager.status(manager.get("tiny"))["status"] == "installed"
