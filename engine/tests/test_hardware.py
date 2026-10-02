"""The machine profile, read from nvidia-smi output and the system's memory figures.

One fixture in fixtures/nvidia-smi/, gtx1070-recorded.txt, was recorded on the owner's GTX 1070
for spec 001's hardware report (specs/001-runtime-manager/reports/2026-09-29-gtx1070.md). The others
are written from nvidia-smi's documented CSV format (`--format=csv,noheader,nounits`), not recorded
on a real card: the cloud sessions they were written in have none. fixtures/meminfo/linux-recorded.txt
is /proc/meminfo recorded in a cloud session; the Windows figures are stubbed."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from fixtures import machines

from oneframe import RUNTIMES_DIR, hardware, runtimes

FIXTURES = Path(__file__).parent / "fixtures" / "nvidia-smi"
MEMINFO = Path(__file__).parent / "fixtures" / "meminfo" / "linux-recorded.txt"
GB = 1_000_000_000


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _runner(*answers: tuple[int, str]) -> tuple[list[list[str]], hardware.Run]:
    """A stand-in for subprocess.run that answers each call with the next (exit code, output)."""
    calls: list[list[str]] = []
    queue = list(answers)

    def run(argv: list[str], **_kw: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        code, out = queue.pop(0)
        return subprocess.CompletedProcess(
            argv, code, stdout=out if code == 0 else "", stderr=out if code else ""
        )

    return calls, run


def _found(_name: str) -> str:
    return "/usr/bin/nvidia-smi"


def test_one_gpu_is_read_from_nvidia_smi() -> None:
    calls, run = _runner((0, _fixture("one-gpu.txt")))
    prof = hardware.profile(run=run, platform="win32", which=_found)
    assert prof["os"] == "windows"
    assert prof["driver"] == "560.94"
    assert prof["nvidia"]["found"] is True
    assert prof["gpus"] == [
        {
            "index": 0,
            "vendor": "nvidia",
            "name": "NVIDIA GeForce GTX 1070",
            "capability": "6.1",
            "vram_total_mb": 8590,  # 8192 MiB
            "vram_free_mb": 7685,
        }
    ]
    assert "compute_cap" in calls[0][1] and calls[0][2] == "--format=csv,noheader,nounits"
    assert prof["raw"].strip() == _fixture("one-gpu.txt").strip()


def test_two_gpus_are_read_from_nvidia_smi() -> None:
    _calls, run = _runner((0, _fixture("two-gpus.txt")))
    prof = hardware.profile(run=run, platform="linux", which=_found)
    assert [(g["index"], g["capability"], g["vram_total_mb"]) for g in prof["gpus"]] == [
        (0, "6.1", 8590),
        (1, "8.6", 12885),
    ]
    assert prof["driver"] == "580.88"
    assert "2 cards" in prof["nvidia"]["why"]


def test_output_recorded_on_the_owners_gtx_1070_is_read() -> None:
    _calls, run = _runner((0, _fixture("gtx1070-recorded.txt")))
    prof = hardware.profile(run=run, platform="win32", which=_found)
    assert [(g["vendor"], g["capability"]) for g in prof["gpus"]] == [("nvidia", "6.1")]
    assert prof["gpus"][0]["vram_total_mb"] > 8000
    assert prof["driver"] == "582.66"
    torch = runtimes.load(RUNTIMES_DIR / "torch" / "runtime.json")
    assert runtimes.plan(torch, prof).build == "cu126"


def test_no_nvidia_smi_means_no_gpu_not_an_error() -> None:
    calls, run = _runner()
    prof = hardware.profile(run=run, platform="linux", which=lambda _name: None)
    assert prof["gpus"] == [] and prof["driver"] is None
    assert prof["nvidia"] == {"found": False, "why": "nvidia-smi was not found, so no NVIDIA card is known."}
    assert calls == []


def test_a_driver_too_old_for_compute_cap_is_asked_again_without_it() -> None:
    calls, run = _runner(
        (2, _fixture("old-driver-no-compute-cap.txt")), (0, _fixture("old-driver-without-compute-cap.txt"))
    )
    prof = hardware.profile(run=run, platform="linux", which=_found)
    assert "compute_cap" in calls[0][1] and "compute_cap" not in calls[1][1]
    assert prof["driver"] == "470.82.01"
    assert prof["gpus"][0]["capability"] is None
    assert prof["gpus"][0]["vram_total_mb"] == 11997
    assert "too old to report compute capability" in prof["nvidia"]["why"]


def test_a_driver_that_is_not_loaded_is_no_gpu_with_the_reason() -> None:
    _calls, run = _runner((9, _fixture("driver-not-loaded.txt")))
    prof = hardware.profile(run=run, platform="linux", which=_found)
    assert prof["gpus"] == []
    assert prof["nvidia"]["found"] is False
    assert "exit 9" in prof["nvidia"]["why"] and "couldn't communicate" in prof["nvidia"]["why"]


def test_nvidia_smi_that_hangs_is_no_gpu_with_the_reason() -> None:
    def hang(argv: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(argv, kw["timeout"])

    prof = hardware.profile(run=hang, platform="linux", which=_found)
    assert prof["gpus"] == []
    assert "did not answer within 10 s" in prof["nvidia"]["why"]


def test_a_name_with_a_comma_is_kept_whole() -> None:
    gpus, driver = hardware.parse_gpus("3, Card, Rev B, 7.5, 4096, 4000, 575.10\n")
    assert gpus[0]["name"] == "Card, Rev B" and gpus[0]["capability"] == "7.5"
    assert gpus[0]["index"] == 3 and driver == "575.10"


def test_unknown_memory_is_none_not_an_error() -> None:
    gpus, _driver = hardware.parse_gpus("0, Card, 8.9, [N/A], [N/A], 580.10\n")
    assert gpus[0]["vram_total_mb"] is None and gpus[0]["vram_free_mb"] is None


def test_free_disk_is_read_from_the_nearest_folder_that_exists(tmp_path: Path) -> None:
    free = hardware.disk_free_mb(tmp_path / "not" / "yet" / "made")
    assert isinstance(free, int) and free > 0
    assert hardware.disk_free_mb(None) is None


def test_the_legacy_windows_folder_is_searched(tmp_path: Path) -> None:
    exe = tmp_path / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"")
    env = {"ProgramFiles": str(tmp_path)}
    assert hardware.find_nvidia_smi("win32", lambda _name: None, env) == str(exe)
    assert hardware.find_nvidia_smi("linux", lambda _name: None, env) is None


def test_a_driver_version_that_is_not_a_number_is_none() -> None:
    _gpus, driver = hardware.parse_gpus("0, Card, 8.6, 8192, 8000, [N/A]\n")
    assert driver is None


# -- system memory -------------------------------------------------------------------------------


def test_linux_system_memory_is_mem_available_from_proc_meminfo() -> None:
    text = MEMINFO.read_text(encoding="utf-8")
    memory = hardware.system_memory("linux", read=lambda: text)
    # MemTotal 16480972 kB and MemAvailable 15965084 kB; kB are KiB, MB are 10^6 bytes.
    assert memory["total_mb"] == 16877 and memory["free_mb"] == 16348
    assert "MemAvailable" in memory["why"]


def test_a_kernel_without_mem_available_leaves_system_memory_unknown() -> None:
    memory = hardware.system_memory("linux", read=lambda: "MemTotal: 8000000 kB\nMemFree: 100 kB\n")
    assert memory["total_mb"] is None and memory["free_mb"] is None
    assert "MemAvailable" in memory["why"]


def _status(phys: int, avail_phys: int, commit: int, avail_commit: int) -> dict[str, int]:
    return {
        "total_phys": phys,
        "avail_phys": avail_phys,
        "total_commit": commit,
        "avail_commit": avail_commit,
    }


def test_windows_system_memory_is_available_physical_memory() -> None:
    memory = hardware.system_memory("win32", read=lambda: _status(32 * GB, 20 * GB, 64 * GB, 40 * GB))
    assert memory == {
        "total_mb": 32000,
        "free_mb": 20000,
        "why": "Read from GlobalMemoryStatusEx (available physical memory).",
    }


def test_windows_commit_smaller_than_physical_memory_is_what_is_free() -> None:
    # A small page file: Windows refuses allocations past the commit limit with memory still free.
    memory = hardware.system_memory("win32", read=lambda: _status(16 * GB, 12 * GB, 18 * GB, 5 * GB))
    assert memory["total_mb"] == 16000 and memory["free_mb"] == 5000
    assert "commit limit leaves 5000 MB" in memory["why"]


def test_a_reader_that_fails_leaves_system_memory_unknown_with_the_reason() -> None:
    def broken() -> str:
        raise PermissionError("no access to /proc/meminfo")

    memory = hardware.system_memory("linux", read=broken)
    assert memory["total_mb"] is None and memory["free_mb"] is None
    assert "no access to /proc/meminfo" in memory["why"]


def test_other_systems_have_system_memory_unknown() -> None:
    memory = hardware.system_memory("darwin")
    assert memory["total_mb"] is None and "macos" in memory["why"]


@pytest.mark.skipif(sys.platform == "win32", reason="on Windows the real reader answers")
def test_asking_for_windows_memory_elsewhere_never_reaches_windll() -> None:
    _calls, run = _runner((0, _fixture("one-gpu.txt")))
    prof = hardware.profile(run=run, platform="win32", which=_found)
    assert prof["system"]["total_mb"] is None and "Windows" in prof["system"]["why"]


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="read on Linux and Windows only")
def test_this_machines_system_memory_is_read() -> None:
    memory = hardware.system_memory()
    assert memory["total_mb"] and memory["free_mb"] is not None
    assert 0 < memory["free_mb"] <= memory["total_mb"] * 2  # commit can exceed physical memory


def test_the_profile_carries_system_memory_from_the_reader() -> None:
    _calls, run = _runner((0, _fixture("one-gpu.txt")))
    prof = hardware.profile(
        run=run, platform="win32", which=_found, read_memory=lambda: _status(8 * GB, 3 * GB, 9 * GB, 4 * GB)
    )
    assert prof["system"]["total_mb"] == 8000 and prof["system"]["free_mb"] == 3000


# -- the test machines ---------------------------------------------------------------------------


def test_the_test_machines_have_the_shape_of_a_real_profile() -> None:
    _calls, run = _runner((0, _fixture("two-gpus.txt")))
    real = hardware.profile(
        run=run, platform="linux", which=_found, read_memory=lambda: MEMINFO.read_text(encoding="utf-8")
    )
    for name, prof in machines.MACHINES.items():
        assert prof.keys() == real.keys(), name
        assert prof["system"].keys() == real["system"].keys(), name
        for gpu in prof["gpus"]:
            assert gpu.keys() == real["gpus"][0].keys(), name
            assert 0 < gpu["vram_free_mb"] <= gpu["vram_total_mb"], name
        assert 0 < prof["system"]["free_mb"] <= prof["system"]["total_mb"], name


def test_the_test_machines_span_no_gpu_to_the_largest_cards() -> None:
    totals = [g["vram_total_mb"] for p in machines.MACHINES.values() for g in p["gpus"]]
    capabilities = {g["capability"] for p in machines.MACHINES.values() for g in p["gpus"]}
    assert any(not p["gpus"] for p in machines.MACHINES.values())
    assert min(totals) <= 2147 and max(totals) >= 85899
    assert {"5.2", "6.1", "7.5", "8.6", "8.9", "9.0"} <= capabilities
    assert any(len(p["gpus"]) > 1 for p in machines.MACHINES.values())


def test_renaming_changes_only_the_names() -> None:
    for prof in machines.MACHINES.values():
        renamed = machines.renamed(prof)
        assert [g["name"] for g in renamed["gpus"]] != [g["name"] for g in prof["gpus"]] or not prof["gpus"]
        strip = [{k: v for k, v in g.items() if k != "name"} for g in renamed["gpus"]]
        assert strip == [{k: v for k, v in g.items() if k != "name"} for g in prof["gpus"]]
        assert {k: v for k, v in renamed.items() if k != "gpus"} == {
            k: v for k, v in prof.items() if k != "gpus"
        }


def test_a_test_machine_is_a_copy_a_test_may_change() -> None:
    small = machines.with_system(machines.get("12 GB, compute 8.6"), 4000, 4295)
    assert small["system"]["free_mb"] == 4000
    assert machines.MACHINES["12 GB, compute 8.6"]["system"]["free_mb"] == 24000
