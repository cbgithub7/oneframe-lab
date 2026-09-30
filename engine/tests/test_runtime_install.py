"""Installing runtimes for real: the tiny runtime from its committed lock, with the real uv, into a
temporary data root. uv's cache and Pythons are shared by the session (see conftest)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from conftest import (
    AMPERE,
    NO_GPU,
    TINY_RUNTIME,
    Events,
    MakeNode,
    SourceServer,
    add_source,
    upstream_archive,
)

from oneframe import runtime_install, runtimes
from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.registry import discover
from oneframe.scheduler import Scheduler


def _manager(
    data: Path, tiny: Path, uv_exe: str, uv_home: Path, profile: dict[str, Any] = NO_GPU
) -> runtimes.Runtimes:
    return runtimes.Runtimes(data, [tiny.parent], uv=uv_exe, profile=lambda: profile, uv_home=uv_home)


def _run(python: Path, code: str) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "PYTHONPATH", "PYTHONHOME")}
    out = subprocess.run(
        [str(python), "-c", code], capture_output=True, text=True, encoding="utf-8", env=env, check=True
    )
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
    hashes = manager.get("tiny").hashes()
    assert {k: marker[k] for k in hashes} == hashes and set(hashes) == {
        "lock_sha256",
        "definition_sha256",
        "stand_ins_sha256",
    }

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

    stand_in = tiny / "stand-ins" / "tinyext" / "__init__.py"
    stand_in.write_text(stand_in.read_text(encoding="utf-8") + "FIXED = True\n", encoding="utf-8")
    assert manager.list()["runtimes"][0]["status"] == "out of date"


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


# A node that runs in the tiny runtime and reports where it is: which Python, which packages, its
# runtime's variable, and what happens when it tries to reach the network.
TINY_NODE = """
import json, os, socket, sys
import idna, tinyext, upstream_pkg

def run(ctx):
    try:
        socket.create_connection(("203.0.113.7", 443), timeout=2)
        network = "open"
    except Exception as exc:
        network = type(exc).__name__
    path = ctx.path("where.json")
    path.write_text(json.dumps({
        "python": list(sys.version_info[:2]), "prefix": sys.prefix, "idna": idna.__version__,
        "tinyext": tinyext.KIND, "upstream": upstream_pkg.WHERE, "env": os.environ.get("ONEFRAME_TINY"),
        "network": network,
    }), encoding="utf-8")
    ctx.output("text", path)
"""


def _tiny_node(make_node: MakeNode) -> Graph:
    make_node(
        {
            "id": "test.in_tiny",
            "version": "1",
            "title": "Runs in the tiny runtime",
            "category": "test",
            "outputs": {"text": "Text"},
            "run": {"where": "runtime", "runtime": "tiny", "entry": "node.py:run"},
        },
        TINY_NODE,
    )
    return Graph.from_json({"version": 1, "nodes": {"n": {"node": "test.in_tiny"}}, "edges": []})


def test_a_runtime_installs_from_its_lock_and_runs_a_node(
    tmp_path: Path,
    tiny: Path,
    uv_exe: str,
    uv_home: Path,
    source_server: SourceServer,
    make_node: MakeNode,
    node_root: Path,
) -> None:
    archive = upstream_archive("from the pinned source")
    source_server.files["/up.tar.gz"] = archive
    add_source(tiny, source_server.url("/up.tar.gz"), archive)
    data = tmp_path / "data"
    manager = _manager(data, tiny, uv_exe, uv_home)
    assert manager.install("tiny") is not None
    assert (data / "runtimes" / "tiny" / "cpu" / runtime_install.MARKER).is_file()

    graph = _tiny_node(make_node)
    scheduler = Scheduler(
        discover([node_root]),
        Cache(data / "cache"),
        runtime_python=manager.python_for,
        runtime_env=manager.env_for,
        log_dir=data / "logs",
    )
    events = Events()
    result = scheduler.run(graph, events.append)

    assert result.status == "done", result.error
    seen = json.loads(result.outputs["n"]["text"].path.read_text(encoding="utf-8"))
    assert seen["python"] == [3, 11]  # the runtime's own Python, not the engine's 3.14
    assert Path(seen["prefix"]).resolve() == (data / "runtimes" / "tiny" / "cpu").resolve()
    assert (seen["idna"], seen["tinyext"], seen["upstream"]) == ("3.20", "stand-in", "from the pinned source")
    assert seen["env"] == "on"
    assert seen["network"] == "NetworkForbidden"


def test_a_node_whose_runtime_is_not_installed_fails_with_the_reason(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, make_node: MakeNode, node_root: Path
) -> None:
    graph = _tiny_node(make_node)
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    scheduler = Scheduler(
        discover([node_root]),
        Cache(tmp_path / "cache"),
        runtime_python=manager.python_for,
        runtime_env=manager.env_for,
    )
    events = Events()
    result = scheduler.run(graph, events.append)
    assert result.status == "failed" and result.error is not None
    assert (result.error["kind"], result.error["reason"]) == ("runtime", "not installed")
    [failed] = events.of("node.failed")
    assert failed["reason"] == "not installed"
    assert "install its cpu build first" in failed["message"]


def test_a_failed_sync_keeps_what_was_installed(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rebuilding an out-of-date runtime offline must not cost the environment already there."""
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    manager.install("tiny")
    env = tmp_path / "data" / "runtimes" / "tiny" / "cpu"
    (tiny / "uv.lock").write_bytes((tiny / "uv.lock").read_bytes() + b"# changed\n")
    real = runtime_install.run_step

    def offline(argv: list[str], *rest: Any) -> tuple[int, list[str]]:
        if argv[1] == "sync":
            return 2, ["error: Failed to fetch: network unreachable"]
        return real(argv, *rest)

    monkeypatch.setattr(runtime_install, "run_step", offline)
    events = Events()
    assert manager.install("tiny", emit=events.append) is None
    assert events.of("runtime.failed")[0]["detail"] == "error: Failed to fetch: network unreachable"
    assert (env / "pyvenv.cfg").is_file()
    assert (
        _run(runtime_install.interpreter(env), "import json, idna; print(json.dumps(idna.__version__))")
        == "3.20"
    )


def test_a_source_file_that_does_not_compile_does_not_fail_the_install(
    tmp_path: Path, tiny: Path, uv_exe: str, uv_home: Path, source_server: SourceServer
) -> None:
    import io
    import tarfile

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, text in (
            ("up-1/upstream_pkg/__init__.py", "WHERE = 'ok'\n"),
            ("up-1/tools/py2.py", "print 'old'\n"),
        ):
            data = text.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    archive = buffer.getvalue()
    source_server.files["/up.tar.gz"] = archive
    add_source(tiny, source_server.url("/up.tar.gz"), archive)
    manager = _manager(tmp_path / "data", tiny, uv_exe, uv_home)
    events = Events()
    assert manager.install("tiny", emit=events.append) is not None
    assert any("did not compile" in e["line"] for e in events.of("runtime.log"))
    env = tmp_path / "data" / "runtimes" / "tiny" / "cpu"
    assert (
        _run(
            runtime_install.interpreter(env),
            "import json, upstream_pkg; print(json.dumps(upstream_pkg.WHERE))",
        )
        == "ok"
    )


def test_with_several_cards_a_node_runs_on_the_card_the_plan_was_made_for(tmp_path: Path, tiny: Path) -> None:
    two = {
        **AMPERE,
        "gpus": [
            {"index": 0, "vendor": "nvidia", "name": "a", "capability": "8.6", "vram_total_mb": 8000},
            {"index": 1, "vendor": "nvidia", "name": "b", "capability": "8.9", "vram_total_mb": 24000},
        ],
    }
    several = runtimes.Runtimes(tmp_path / "data", [tiny.parent], profile=lambda: two)
    assert several.env_for("tiny") == {
        "ONEFRAME_TINY": "on",
        "CUDA_DEVICE_ORDER": "PCI_BUS_ID",
        "CUDA_VISIBLE_DEVICES": "1",
    }
    one = runtimes.Runtimes(tmp_path / "data", [tiny.parent], profile=lambda: AMPERE)
    assert one.env_for("tiny") == {"ONEFRAME_TINY": "on"}
    none = runtimes.Runtimes(tmp_path / "data", [tiny.parent], profile=lambda: NO_GPU)
    assert none.env_for("tiny") == {"ONEFRAME_TINY": "on"}
