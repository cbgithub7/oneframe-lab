"""Runtime definitions and the plan: which build of a runtime a machine gets, and why."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any

import pytest
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
    make_runtime(_definition(id="fine"), lock=_lock_with_torch("2.10.0+cu126", "2.14.0"))
    found = runtimes.discover([runtime_root])
    assert list(found.runtimes) == ["fine"]
    [problems] = found.problems.values()
    assert problems == [
        "uv.lock holds torch 2.5.1; every runtime needs torch 2.10 or newer "
        "(older torch can run code from a crafted weights file, even with weights_only: "
        "CVE-2025-32434, CVE-2026-24747)"
    ]


def test_a_lock_holding_torch_older_than_2_10_is_refused(
    make_runtime: MakeRuntime, runtime_root: Path
) -> None:
    """CVE-2026-24747: before 2.10, torch's weights_only unpickler could corrupt memory."""
    make_runtime(_definition(), lock=_lock_with_torch("2.9.1+cu126"))
    found = runtimes.discover([runtime_root])
    assert found.runtimes == {}
    [problems] = found.problems.values()
    assert problems[0].startswith("uv.lock holds torch 2.9.1+cu126; every runtime needs torch 2.10")


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
    lf = lock.read_bytes()
    assert b"\r" not in lf
    lock.write_bytes(lf.replace(b"\n", b"\r\n"))
    assert runtime.hashes() == before
    lock.write_bytes(lock.read_bytes() + b"# changed\n")
    assert runtime.hashes()["lock_sha256"] != before["lock_sha256"]


# -- the plan ---------------------------------------------------------------------------------

# The test runtime of AC1: cpu, cu126 (compute 5.0 to 12.x, driver 528 or newer) and cu130
# (compute 7.5 or newer, driver 580 or newer).
AC1_BUILDS = [
    {"name": "cu130", "vendor": "nvidia", "min_capability": "7.5", "min_driver": "580"},
    {
        "name": "cu126",
        "vendor": "nvidia",
        "min_capability": "5.0",
        "max_capability": "12",
        "min_driver": "528",
    },
    {"name": "cpu", "vendor": "none"},
]
NAME = "Zorblax 9000 Ti"  # a card nobody makes: it must not reach any plan


def _machine(
    capability: str | None = None,
    driver: str | None = None,
    os: str = "windows",
    disk: int | None = 500_000,
    gpus: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if gpus is None:
        gpus = (
            []
            if capability is None and driver is None
            else [
                {
                    "index": 0,
                    "vendor": "nvidia",
                    "name": NAME,
                    "capability": capability,
                    "vram_total_mb": 8590,
                }
            ]
        )
    return {
        "os": os,
        "gpus": gpus,
        "driver": driver,
        "nvidia": {
            "found": bool(gpus),
            "why": "nvidia-smi listed a card." if gpus else "nvidia-smi was not found.",
        },
        "disk_free_mb": disk,
        "raw": f"0, {NAME}, {capability}, 8192, 8000, {driver}\n" if gpus else "",
    }


def _runtime(root: Path, **over: Any) -> runtimes.RuntimeDef:
    folder = write_runtime(root, _definition(**{"builds": AC1_BUILDS, **over}))
    return runtimes.load(folder / "runtime.json")


def _why(p: runtimes.Plan, build: str) -> str:
    return next(row["why"] for row in p.considered if row["build"] == build)


@pytest.mark.parametrize(
    ("capability", "driver", "build"),
    [("6.1", "560.94", "cu126"), ("8.6", "580.88", "cu130"), ("8.6", "560.94", "cu126"), (None, None, "cpu")],
)
def test_the_plan_picks_the_build_each_machine_can_run(
    tmp_path: Path, capability: str | None, driver: str | None, build: str
) -> None:
    p = runtimes.plan(_runtime(tmp_path), _machine(capability, driver))
    assert (p.build, p.blocked) == (build, None)
    assert [row["build"] for row in p.considered if row["chosen"]] == [build]


def test_a_runtime_without_a_cpu_build_is_blocked_by_an_old_driver_and_says_so(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path, builds=AC1_BUILDS[:2])
    p = runtimes.plan(runtime, _machine("6.1", "470.82.01"))
    assert p.build is None
    assert p.blocked == (
        "No build of Demo runs on this machine: "
        "cu130 needs NVIDIA driver 580 or newer; this machine has 470.82.01; "
        "cu126 needs NVIDIA driver 528 or newer; this machine has 470.82.01."
    )


def test_a_refused_build_says_whether_the_card_or_the_driver_refused_it(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    old_card = runtimes.plan(runtime, _machine("6.1", "580.88"))
    assert _why(old_card, "cu130") == "cu130 needs compute capability 7.5 or higher; this card is 6.1"
    old_driver = runtimes.plan(runtime, _machine("8.6", "560.94"))
    assert _why(old_driver, "cu130") == "cu130 needs NVIDIA driver 580 or newer; this machine has 560.94"
    assert _why(old_driver, "cpu") == "a GPU build runs here"


def test_no_gpu_name_reaches_a_plan(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    for machine in (
        _machine("6.1", "560.94"),
        _machine("8.6", "580.88"),
        _machine("6.1", "470.00"),
        _machine(),
    ):
        assert NAME not in json.dumps(runtimes.plan(runtime, machine).to_json())


def test_capabilities_compare_as_numbers_and_a_bare_major_covers_all_of_it(tmp_path: Path) -> None:
    builds = [{"name": "gpu", "vendor": "nvidia", "min_capability": "12.9", "max_capability": "13"}]
    runtime = _runtime(tmp_path, builds=builds)
    assert runtimes.plan(runtime, _machine("12.10", "600")).build == "gpu"
    assert runtimes.plan(runtime, _machine("13.9", "600")).build == "gpu"
    refused = runtimes.plan(runtime, _machine("12.8", "600"))
    assert refused.blocked is not None and "needs compute capability 12.9 or higher" in refused.blocked
    exact = _runtime(tmp_path / "x", builds=[{"name": "gpu", "vendor": "nvidia", "max_capability": "12.0"}])
    assert runtimes.plan(exact, _machine("12.1", "600")).blocked == (
        "No build of Demo runs on this machine: gpu covers compute capability up to 12.0; this card is 12.1."
    )


def test_driver_floors_can_differ_by_os(tmp_path: Path) -> None:
    builds = [
        {"name": "cu126", "vendor": "nvidia", "min_driver": {"windows": "528.33", "linux": "525.60.13"}},
        {"name": "cpu", "vendor": "none"},
    ]
    runtime = _runtime(tmp_path, builds=builds)
    assert runtimes.plan(runtime, _machine("6.1", "525.60.13", os="linux")).build == "cu126"
    windows = runtimes.plan(runtime, _machine("6.1", "528.10", os="windows"))
    assert windows.build == "cpu"
    assert _why(windows, "cu126") == "cu126 needs NVIDIA driver 528.33 or newer; this machine has 528.10"
    assert _why(runtimes.plan(runtime, _machine("6.1", "600", os="macos")), "cu126") == (
        "cu126 is not offered on macos"
    )


def test_a_card_too_old_to_report_its_capability_gets_the_processor_build(tmp_path: Path) -> None:
    p = runtimes.plan(
        _runtime(tmp_path),
        _machine(
            None,
            "470.82.01",
            gpus=[{"index": 0, "vendor": "nvidia", "name": NAME, "capability": None, "vram_total_mb": 11997}],
        ),
    )
    assert p.build == "cpu"
    assert _why(p, "cu130").startswith("cu130 needs NVIDIA driver 580")


def test_with_several_cards_the_plan_is_for_the_one_with_the_most_memory(tmp_path: Path) -> None:
    gpus = [
        {"index": 0, "vendor": "nvidia", "name": NAME, "capability": "6.1", "vram_total_mb": 8590},
        {"index": 1, "vendor": "nvidia", "name": NAME, "capability": "8.6", "vram_total_mb": 12885},
    ]
    p = runtimes.plan(_runtime(tmp_path), _machine(driver="580.88", gpus=gpus))
    assert p.build == "cu130"
    assert "Planned for card 1 of 2, the one with the most memory." in p.notes


def test_a_required_extension_blocks_the_plan_and_says_what_it_needs(tmp_path: Path) -> None:
    extensions = [
        {"package": "raster", "class": "required", "for": "texture baking", "needs": "a C++ compiler"},
        {
            "package": "flash",
            "class": "optional",
            "for": "fast attention",
            "why": "PyTorch attention is used instead",
        },
    ]
    p = runtimes.plan(_runtime(tmp_path, extensions=extensions), _machine("8.6", "580.88"))
    assert p.build is None
    assert p.blocked == (
        "Demo needs raster (texture baking), which has to be compiled: that takes a C++ compiler. "
        "Prebuilt wheels are not supported yet."
    )
    assert _why(p, "cu130") == "cu130 fits this machine, but it cannot be installed."
    assert "flash (fast attention) is left out: PyTorch attention is used instead" in p.notes


def test_an_optional_extension_is_noted_and_does_not_block(tmp_path: Path) -> None:
    extensions = [{"package": "flash", "class": "optional", "for": "fast attention", "why": "slower without"}]
    p = runtimes.plan(_runtime(tmp_path, extensions=extensions), _machine("8.6", "580.88"))
    assert (p.build, p.blocked) == ("cu130", None)
    assert p.notes == ["flash (fast attention) is left out: slower without"]


def test_too_little_free_disk_blocks_a_build_that_is_not_installed(tmp_path: Path) -> None:
    builds = [{**AC1_BUILDS[1], "disk_mb": 6000}, {"name": "cpu", "vendor": "none", "disk_mb": 1500}]
    runtime = _runtime(tmp_path, builds=builds)
    short = runtimes.plan(runtime, _machine("6.1", "560.94", disk=3000))
    assert short.blocked == "cu126 needs about 6000 MB, and the data root has 3000 MB free."
    assert runtimes.plan(runtime, _machine("6.1", "560.94", disk=3000), installed={"cu126"}).build == "cu126"
    assert runtimes.plan(runtime, _machine("6.1", "560.94", disk=7000)).build == "cu126"


def test_the_processor_build_keeps_to_its_systems_too(tmp_path: Path) -> None:
    builds = [AC1_BUILDS[1], {"name": "cpu", "vendor": "none", "os": ["linux"]}]
    runtime = _runtime(tmp_path, builds=builds)
    assert runtimes.plan(runtime, _machine(os="linux")).build == "cpu"
    windows = runtimes.plan(runtime, _machine(os="windows"))
    assert windows.build is None
    assert windows.blocked is not None and "cpu runs on linux only" in windows.blocked


def test_a_driver_version_that_is_not_a_version_counts_as_unknown(tmp_path: Path) -> None:
    p = runtimes.plan(_runtime(tmp_path), _machine("8.6", "[N/A]"))
    assert p.build == "cpu"
    assert _why(p, "cu130") == "cu130 needs NVIDIA driver 580 or newer, and the driver version is unknown"


def test_a_definition_is_read_again_only_when_its_files_change(make_runtime: MakeRuntime) -> None:
    folder = make_runtime(_definition())
    first = runtimes.load(folder / "runtime.json")
    assert runtimes.load(folder / "runtime.json") is first
    (folder / "uv.lock").write_text(
        'version = 1\nrequires-python = ">=3.12"\n', encoding="utf-8", newline="\n"
    )
    assert runtimes.load(folder / "runtime.json") is not first


@pytest.mark.parametrize(
    ("capability", "driver", "build"),
    [
        ("6.1", "560.94", "cu126"),  # the owner's GTX 1070
        ("6.1", "582.66", "cu126"),  # a Pascal card on a new driver: CUDA 13 has no kernels for it
        ("8.6", "560.94", "cu126"),  # a new card on a driver older than 580
        ("8.6", "580.88", "cu130"),
        ("12.0", "580.88", "cu130"),  # Blackwell: only the CUDA 13 build has its kernels
        ("12.0", "560.94", "cpu"),  # ... so on an old driver it gets no GPU build, and says why
        (None, None, "cpu"),
    ],
)
def test_the_torch_runtime_follows_the_owners_rule(
    capability: str | None, driver: str | None, build: str
) -> None:
    """Spec 001: cu126 below compute 7.5 or on a driver older than 580, otherwise cu130. The floor
    is torch 2.10, and every build is locked at the same torch."""
    torch = runtimes.discover([RUNTIMES_DIR]).runtimes["torch"]
    assert runtimes.plan(torch, _machine(capability, driver)).build == build
    lock = tomllib.loads(torch.lock_path.read_text(encoding="utf-8"))
    versions = {p["version"] for p in lock["package"] if p["name"] == "torch"}
    assert versions == {"2.14.0+cpu", "2.14.0+cu126", "2.14.0+cu130"}
