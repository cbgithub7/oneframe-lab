"""Runtime definitions and the plan: which build of a runtime a machine gets, and why."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conftest import TINY_RUNTIME, MakeRuntime, write_runtime

from oneframe import RUNTIMES_DIR, runtimes


def _definition(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": "demo",
        "title": "Demo",
        "python": "3.12",
        "builds": [
            {
                "name": "cu130",
                "vendor": "nvidia",
                "min_capability": "7.5",
                "max_capability": "12",
                "min_driver": "580",
            },
            {
                "name": "cu126",
                "vendor": "nvidia",
                "min_capability": "5.0",
                "max_capability": "12",
                "min_driver": "528",
            },
            {"name": "cpu", "vendor": "none"},
        ],
    }
    base.update(over)
    return base


def test_the_test_runtime_reads_without_problems() -> None:
    found = runtimes.discover([TINY_RUNTIME.parent])
    assert found.problems == {}
    tiny = found.runtimes["tiny"]
    assert tiny.python == "3.11"
    assert [b.name for b in tiny.builds] == ["cu130", "cpu"]
    assert tiny.build("cu130") == runtimes.Build("cu130", "nvidia", "7.5", "12", "580", (), 1)
    assert [(e.package, e.kind) for e in tiny.extensions] == [
        ("tinyext", "stand-in"),
        ("fastpath", "optional"),
    ]
    assert tiny.env == {"ONEFRAME_TINY": "on"} and tiny.probe == "probe.py:run"


def test_every_runtime_in_the_repo_reads_without_problems() -> None:
    assert runtimes.discover([RUNTIMES_DIR]).problems == {}


def test_a_broken_runtime_does_not_hide_the_others(make_runtime: MakeRuntime, runtime_root: Path) -> None:
    make_runtime(_definition())
    make_runtime(_definition(id="broken", python="three"))
    found = runtimes.discover([runtime_root])
    assert list(found.runtimes) == ["demo"]
    [(path, problems)] = found.problems.items()
    assert path.endswith(str(Path("broken") / "runtime.json"))
    assert problems == ["python 'three' should be a CPython version like 3.12"]


def test_every_problem_in_a_definition_is_listed_at_once(
    make_runtime: MakeRuntime, runtime_root: Path
) -> None:
    folder = make_runtime(
        _definition(
            id="many",
            builds=[
                {"name": "cu126", "vendor": "nvidia", "min_driver": {"windows": "528.33", "beos": "1"}},
                {"name": "cu126", "vendor": "nvidia"},
                {"name": "rocm", "vendor": "amd", "min_capability": "gfx90a"},
                {"name": "cpu", "vendor": "none", "min_driver": "1"},
                {"name": "cpu2", "vendor": "none", "disk_mb": -1},
                {"name": "npu", "vendor": "npu"},
            ],
            extensions=[
                {"package": "a", "class": "stand-in", "for": "x", "stand_in": "stand-ins/a"},
                {"package": "b", "class": "optional", "for": "x"},
                {"package": "c", "class": "required", "for": "x"},
                {"package": "d", "class": "maybe", "for": "x"},
            ],
            sources=[
                {"name": "../up", "url": "https://x", "sha256": "0" * 64},
                {"name": "up", "url": "ftp://x", "sha256": "0" * 64},
                {"name": "up2", "url": "https://x", "sha256": "ABC"},
            ],
            env={"FLAG": 1},
            probe="missing.py:run",
        ),
        extras=["cu126"],
        package=True,
    )
    (folder / "uv.lock").unlink()
    [problems] = runtimes.discover([runtime_root]).problems.values()
    expected = [
        "builds.cu126.min_driver per OS should map windows, linux, macos to versions",
        "builds.cu126 is listed twice",
        'builds.rocm.min_capability should be a version like "7.5"',
        "builds.cpu runs on the processor, so it takes no capability or driver",
        "builds.cpu2.disk_mb should be a whole number of MB",
        "builds.npu.vendor should be one of nvidia, amd, intel, none",
        "builds: only one build can run on the processor",
        "extensions.a.stand_in should name a folder inside many/",
        "extensions.b is optional, so it needs a why: what the nodes do without it",
        "extensions.c is required, so it needs a needs: what building it takes",
        "extensions.d.class should be one of stand-in, optional, required",
        "sources[0].name should be one folder name",
        "sources.up.url should be an http(s) address",
        "sources.up2.sha256 should be 64 lower-case hex digits",
        "env should map names to strings",
        "probe should read file.py:function, with the file in many/",
        "uv.lock is missing",
        "pyproject.toml: [tool.uv] package = false is missing; a runtime is not a package",
        "pyproject.toml: no extra for the build(s) rocm, cpu, cpu2",
        "pyproject.toml: the builds cu126, rocm, cpu, cpu2 should be one set in [tool.uv] conflicts",
    ]
    assert problems == expected


def test_a_runtime_id_must_match_its_folder(make_runtime: MakeRuntime, runtime_root: Path) -> None:
    folder = make_runtime(_definition())
    data = json.loads((folder / "runtime.json").read_text(encoding="utf-8"))
    data["id"] = "other"
    (folder / "runtime.json").write_text(json.dumps(data), encoding="utf-8")
    [problems] = runtimes.discover([runtime_root]).problems.values()
    assert problems == ["id 'other' should match its folder, demo"]


def test_builds_must_be_one_conflict_set(runtime_root: Path) -> None:
    folder = write_runtime(runtime_root, _definition())
    pyproject = (folder / "pyproject.toml").read_text(encoding="utf-8")
    (folder / "pyproject.toml").write_text(pyproject.split("conflicts")[0], encoding="utf-8")
    [problems] = runtimes.discover([runtime_root]).problems.values()
    assert problems == [
        "pyproject.toml: the builds cu130, cu126, cpu should be one set in [tool.uv] conflicts"
    ]


def _lock_with_torch(*versions: str) -> str:
    rows = "".join(f'\n[[package]]\nname = "torch"\nversion = "{v}"\n' for v in versions)
    return 'version = 1\nrequires-python = ">=3.11"\n' + rows


def test_a_lock_holding_torch_older_than_2_6_is_refused(
    make_runtime: MakeRuntime, runtime_root: Path
) -> None:
    make_runtime(_definition(), lock=_lock_with_torch("2.14.0", "2.5.1"))
    make_runtime(_definition(id="fine"), lock=_lock_with_torch("2.6.0+cu126", "2.14.0"))
    found = runtimes.discover([runtime_root])
    assert list(found.runtimes) == ["fine"]
    [problems] = found.problems.values()
    assert problems == [
        "uv.lock holds torch 2.5.1; every runtime needs torch 2.6 or newer "
        "(older torch can run code from a crafted weights file, CVE-2025-32434)"
    ]


def test_two_runtimes_cannot_share_an_id(tmp_path: Path) -> None:
    write_runtime(tmp_path / "a", _definition())
    write_runtime(tmp_path / "b", _definition())
    found = runtimes.discover([tmp_path / "a", tmp_path / "b"])
    assert list(found.runtimes) == ["demo"]
    [problems] = found.problems.values()
    assert problems[0].startswith("id 'demo' is already used by")


def test_versions_compare_as_numbers() -> None:
    assert runtimes.version_tuple("12.10") > runtimes.version_tuple("12.9")
    assert runtimes.version_tuple("560.94") > runtimes.version_tuple("528.33")
    assert runtimes.version_tuple("2.6.0+cu126") == (2, 6, 0)


def test_the_hashes_do_not_change_with_line_endings(make_runtime: MakeRuntime) -> None:
    folder = make_runtime(_definition())
    runtime = runtimes.load(folder / "runtime.json")
    before = runtime.hashes()
    lock = folder / "uv.lock"
    lock.write_bytes(lock.read_bytes().replace(b"\n", b"\r\n"))
    assert runtime.hashes() == before
    lock.write_bytes(lock.read_bytes() + b"# changed\n")
    assert runtime.hashes()["lock_sha256"] != before["lock_sha256"]
