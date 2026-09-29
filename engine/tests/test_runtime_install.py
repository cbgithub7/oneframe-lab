"""Installing runtimes for real: the tiny runtime from its committed lock, with the real uv, into a
temporary data root. uv's cache and Pythons are shared by the session (see conftest)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import AMPERE, NO_GPU, TINY_RUNTIME, Events, SourceServer, add_source, upstream_archive

from oneframe import runtime_install, runtimes


def _manager(
    data: Path, tiny: Path, uv_exe: str, uv_home: Path, profile: dict[str, Any] = NO_GPU
) -> runtimes.Runtimes:
    return runtimes.Runtimes(data, [tiny.parent], uv=uv_exe, profile=lambda: profile, uv_home=uv_home)


def _run(python: Path, code: str) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
    out = subprocess.run([str(python), "-c", code], capture_output=True, text=True, env=env, check=True)
    return json.loads(out.stdout)


PROBE = (
    "import json, sys, idna, tinyext, upstream_pkg\n"
    "print(json.dumps({'python': list(sys.version_info[:2]), 'prefix': sys.prefix, 'idna': idna.__version__,"
    " 'tinyext': tinyext.KIND, 'upstream': upstream_pkg.WHERE}))"
)


def test_a_runtime_installs_under_the_data_root_from_its_lock(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, source_server: SourceServer
) -> None:
    archive = upstream_archive()
    source_server.files["/up.tar.gz"] = archive
    add_source(tiny, source_server.url("/up.tar.gz"), archive)
    data = tmp_path / "data"
    manager = _manager(data, tiny, uv_exe, uv_home)
    events = Events()

    done = manager.install("tiny", emit=events.append)

    env = data / "runtimes" / "tiny" / "cpu"
    assert (
        done is not None
        and done["build"] == "cpu"
        and done["python"] == str(runtime_install.interpreter(env))
    )
    assert events.kinds()[0] == "runtime.start" and events.kinds()[-1] == "runtime.done"
    assert [e["step"] for e in events.of("runtime.step")] == list(runtime_install.STEPS)
    assert events.of("runtime.progress")[-1]["done"] == len(archive)

    # the marker is the last thing written (checked before anything runs in the environment)
    marker_time = (env / runtime_install.MARKER).stat().st_mtime_ns
    files = [
        p for p in env.rglob("*") if p.is_file() and not p.is_symlink() and p.name != runtime_install.MARKER
    ]
    assert max(p.stat().st_mtime_ns for p in files) <= marker_time

    seen = _run(runtime_install.interpreter(env), PROBE)
    assert seen["python"] == [3, 11]
    assert Path(seen["prefix"]).resolve() == env.resolve()
    assert (seen["idna"], seen["tinyext"], seen["upstream"]) == ("3.20", "stand-in", "upstream")
    # the sources and stand-ins were compiled at install, so running the runtime writes nothing into it
    after = [
        p for p in env.rglob("*") if p.is_file() and not p.is_symlink() and p.name != runtime_install.MARKER
    ]
    assert sorted(after) == sorted(files)

    marker = json.loads((env / runtime_install.MARKER).read_text(encoding="utf-8"))
    assert marker["build"] == "cpu" and marker["python"].startswith("3.11.")
    assert "idna==3.20" in marker["freeze"]
    assert marker["stand_ins"] == ["tinyext"] and marker["left_out"] == ["fastpath"]
    assert {k: marker[k] for k in ("lock_sha256", "definition_sha256")} == manager._get("tiny").hashes()

    # nothing is written into the definition, and everything else stays under the data root
    assert sorted(p.name for p in tiny.iterdir()) == sorted(p.name for p in TINY_RUNTIME.iterdir())
    assert sorted(p.name for p in (data / "runtimes" / "tiny").iterdir()) == ["cpu", "downloads"]

    [row] = manager.list()["runtimes"]
    assert (row["status"], row["build"], row["size_bytes"]) == ("installed", "cpu", marker["size_bytes"])
    assert manager.python_for("tiny") == runtime_install.interpreter(env)
    assert manager.env_for("tiny") == {"ONEFRAME_TINY": "on"}
    assert manager.install("tiny") == {"runtime": "tiny", "already": True}


def test_an_nvidia_machine_gets_the_other_build_from_the_same_lock(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home, AMPERE)
    done = manager.install("tiny")
    assert done is not None and done["build"] == "cu130"
    python = runtime_install.interpreter(tmp_path / "data" / "runtimes" / "tiny" / "cu130")
    assert _run(python, "import json, idna; print(json.dumps({'idna': idna.__version__}))") == {
        "idna": "3.19"
    }


def test_a_changed_lock_makes_a_runtime_out_of_date(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    manager.install("tiny")
    lock = tiny / "uv.lock"
    original = lock.read_bytes()
    lock.write_bytes(original + b"# changed after the install\n")

    [row] = manager.list()["runtimes"]
    assert row["status"] == "out of date"
    with pytest.raises(runtimes.RuntimeMissing) as caught:
        manager.python_for("tiny")
    assert caught.value.reason == "out of date"
    assert "Install it again to rebuild it" in str(caught.value)

    lock.write_bytes(original)
    assert manager.list()["runtimes"][0]["status"] == "installed"
    definition = tiny / "runtime.json"
    definition.write_text(definition.read_text(encoding="utf-8").replace('"on"', '"off"'), encoding="utf-8")
    assert manager.list()["runtimes"][0]["status"] == "out of date"

    done = manager.install("tiny")  # asked: rebuilt
    assert done is not None and manager.list()["runtimes"][0]["status"] == "installed"


def test_remove_deletes_only_that_runtime(tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path) -> None:
    data = tmp_path / "data"
    manager = _manager(data, tiny, uv_exe, uv_home)
    manager.install("tiny")
    python_home = uv_home / "python"
    pythons_before = sorted(p.name for p in python_home.iterdir())
    keep = [
        data / "runtimes" / "other" / "keep.txt",
        data / "runtimes" / "keep.txt",
        data / "cache" / "keep.txt",
        data / "models" / "keep.txt",
        data / "runtimes-tiny" / "keep.txt",
    ]
    for path in keep:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("keep", encoding="utf-8")

    removed = manager.remove("tiny")

    assert removed == {"runtime": "tiny", "removed": str(data / "runtimes" / "tiny")}
    assert not (data / "runtimes" / "tiny").exists()
    assert all(path.read_text(encoding="utf-8") == "keep" for path in keep)
    assert sorted(p.name for p in python_home.iterdir()) == pythons_before  # the venv's link was not followed
    assert manager.list()["runtimes"][0]["status"] == "not installed"
    assert manager.remove("tiny") == {"runtime": "tiny", "removed": None}
    with pytest.raises(runtimes.RuntimeMissing, match="No runtime called 'elsewhere'"):
        manager.remove("elsewhere")


@pytest.mark.skipif(os.name == "nt", reason="making a symlink needs a privilege on Windows")
def test_remove_refuses_a_runtime_folder_that_is_a_link(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    data = tmp_path / "data"
    precious = tmp_path / "precious"
    (precious / "cpu").mkdir(parents=True)
    (data / "runtimes").mkdir(parents=True)
    (data / "runtimes" / "tiny").symlink_to(precious, target_is_directory=True)
    with pytest.raises(runtimes.InstallRefused, match="not a runtime folder"):
        _manager(data, tiny, uv_exe, uv_home).remove("tiny")
    assert (precious / "cpu").is_dir()


def test_a_failure_before_the_marker_leaves_no_marker(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)

    def broken(*_args: Any) -> list[str]:
        raise runtime_install.InstallFailed("uv pip freeze failed (exit 2).", "simulated")

    monkeypatch.setattr(runtime_install, "_freeze", broken)
    events = Events()
    assert manager.install("tiny", emit=events.append) is None
    [failed] = events.of("runtime.failed")
    assert failed["message"] == "uv pip freeze failed (exit 2)." and failed["detail"] == "simulated"
    env = tmp_path / "data" / "runtimes" / "tiny" / "cpu"
    assert (env / "pyvenv.cfg").is_file() and not (env / runtime_install.MARKER).exists()
    assert manager.list()["runtimes"][0]["status"] == "not installed"

    monkeypatch.undo()
    assert manager.install("tiny") is not None  # installing again finishes it
    assert manager.list()["runtimes"][0]["status"] == "installed"


def test_stop_ends_an_install_without_a_marker_and_frees_the_slot(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path
) -> None:
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    claimed = manager.begin_install("tiny")
    assert claimed is not None and manager.installing() == "tiny"
    assert manager.list()["runtimes"][0]["status"] == "installing"
    with pytest.raises(runtimes.RuntimeMissing) as caught:
        manager.python_for("tiny")
    assert caught.value.reason == "installing"
    with pytest.raises(runtimes.InstallRefused, match="one at a time"):
        manager.begin_install("tiny")
    with pytest.raises(runtimes.InstallRefused, match="being installed"):
        manager.remove("tiny")

    assert manager.stop("tiny") and not manager.stop("other")
    events = Events()
    assert manager.run_install(*claimed, emit=events.append) is None
    assert events.kinds()[-1] == "runtime.stopped"
    assert manager.installing() is None
    assert not (tmp_path / "data" / "runtimes" / "tiny" / "cpu" / runtime_install.MARKER).exists()


def test_only_the_planned_build_is_installed(tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path) -> None:
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    with pytest.raises(runtimes.InstallRefused) as caught:
        manager.begin_install("tiny", "cu130")
    assert str(caught.value) == (
        "This machine runs the cpu build of 'tiny'; only the planned build is installed. "
        "cu130 needs an NVIDIA card, and none was found"
    )
    with pytest.raises(runtimes.InstallRefused, match="no build called 'rocm'"):
        manager.begin_install("tiny", "rocm")
    assert manager.installing() is None


def test_without_uv_an_install_is_refused_and_listing_still_works(tmp_path: Path, tiny: Path) -> None:
    manager = runtimes.Runtimes(tmp_path / "data", [tiny.parent], uv=None, profile=lambda: NO_GPU)
    with pytest.raises(runtimes.InstallRefused, match="uv was not found"):
        manager.begin_install("tiny")
    assert manager.list()["runtimes"][0]["status"] == "not installed"
