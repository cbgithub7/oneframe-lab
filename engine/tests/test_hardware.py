"""The machine profile, read from nvidia-smi output.

The fixtures in fixtures/nvidia-smi/ are written from nvidia-smi's documented CSV format
(`--format=csv,noheader,nounits`), not recorded on a real card: the cloud sessions this was written
in have none. Output recorded on the owner's PC joins them with the spec's hardware report."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from oneframe import hardware

FIXTURES = Path(__file__).parent / "fixtures" / "nvidia-smi"


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
