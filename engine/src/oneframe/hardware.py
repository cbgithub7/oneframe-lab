"""The machine a runtime is planned for: its NVIDIA cards, their driver, the OS and free disk.

Cards are read with nvidia-smi, which every NVIDIA driver installs on Windows and Linux, so
reading them needs no library in the engine and no CUDA context on the card. What is read is what a
plan decides on: each card's compute capability and memory, and the driver version. A card's name
is kept for people to read and nothing else; nothing decides on it, so a card released next year
needs no change here.

A machine without nvidia-smi, or whose driver is not loaded, has no NVIDIA card as far as a plan is
concerned. That is an answer with a reason, never an error: listing runtimes on a laptop without a
GPU works and says why every GPU build was passed over.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

FIELDS = ("index", "name", "compute_cap", "memory.total", "memory.free", "driver_version")
# Drivers older than about R510 do not know compute_cap; they are asked again without it.
FIELDS_WITHOUT_CAPABILITY = tuple(f for f in FIELDS if f != "compute_cap")
TIMEOUT_S = 10
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MIB = 1.048576  # MB per MiB: nvidia-smi counts MiB, the engine reports MB like peak_vram_mb

Run = Callable[..., subprocess.CompletedProcess[str]]


def os_name(platform: str = sys.platform) -> str:
    return {"win32": "windows", "linux": "linux", "darwin": "macos"}.get(platform, platform)


def find_nvidia_smi(
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
) -> str | None:
    """nvidia-smi on PATH or, on Windows, where drivers before R418 put it."""
    found = which("nvidia-smi")
    if found:
        return found
    if platform == "win32":
        program_files = (env if env is not None else os.environ).get("ProgramFiles", r"C:\Program Files")
        legacy = Path(program_files) / "NVIDIA Corporation" / "NVSMI" / "nvidia-smi.exe"
        if legacy.is_file():
            return str(legacy)
    return None


def _number(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None  # "[N/A]", "[Not Supported]"


def _mb(text: str) -> int | None:
    value = _number(text)
    return None if value is None else round(value * MIB)


def parse_gpus(text: str, fields: tuple[str, ...] = FIELDS) -> tuple[list[dict[str, Any]], str | None]:
    """nvidia-smi's `--format=csv,noheader,nounits` lines as cards, and the driver version.

    The name is the only field that could hold a comma, so a line is read from both ends: the index
    first, the numbers last, and the name is what is left between them."""
    gpus: list[dict[str, Any]] = []
    driver: str | None = None
    tail = len(fields) - 2  # fields after index and name
    for line in text.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(fields) or not parts[0].isdigit():
            continue
        row = dict(zip(fields[2:], parts[-tail:], strict=True))
        name = ", ".join(parts[1:-tail])
        capability = row.get("compute_cap")
        gpus.append(
            {
                "index": int(parts[0]),
                "vendor": "nvidia",
                "name": name,
                "capability": capability if capability and _number(capability) is not None else None,
                "vram_total_mb": _mb(row["memory.total"]),
                "vram_free_mb": _mb(row["memory.free"]),
            }
        )
        driver = driver or row.get("driver_version") or None
    return gpus, driver


def _query(exe: str, fields: tuple[str, ...], run: Run) -> subprocess.CompletedProcess[str]:
    return run(
        [exe, f"--query-gpu={','.join(fields)}", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TIMEOUT_S,
        stdin=subprocess.DEVNULL,
        creationflags=NO_WINDOW,
    )


def read_nvidia(
    run: Run = subprocess.run,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """`{"gpus", "driver", "nvidia": {"found", "why"}, "raw"}`, whatever nvidia-smi does."""
    exe = find_nvidia_smi(platform, which, env)
    if exe is None:
        return {
            "gpus": [],
            "driver": None,
            "nvidia": {"found": False, "why": "nvidia-smi was not found, so no NVIDIA card is known."},
            "raw": "",
        }
    raw: list[str] = []
    try:
        fields = FIELDS
        done = _query(exe, fields, run)
        raw.append((done.stdout or "") + (done.stderr or ""))
        if done.returncode != 0 and "compute_cap" in raw[-1]:
            fields = FIELDS_WITHOUT_CAPABILITY
            done = _query(exe, fields, run)
            raw.append((done.stdout or "") + (done.stderr or ""))
    except subprocess.TimeoutExpired:
        why = f"nvidia-smi did not answer within {TIMEOUT_S} s, so no NVIDIA card is known."
        return {"gpus": [], "driver": None, "nvidia": {"found": False, "why": why}, "raw": "\n".join(raw)}
    except OSError as exc:
        why = f"nvidia-smi could not be started ({exc}), so no NVIDIA card is known."
        return {"gpus": [], "driver": None, "nvidia": {"found": False, "why": why}, "raw": "\n".join(raw)}
    text = "\n".join(raw)
    if done.returncode != 0:
        first = next((line.strip() for line in raw[-1].splitlines() if line.strip()), "no output")
        why = f"nvidia-smi failed (exit {done.returncode}): {first}"
        return {"gpus": [], "driver": None, "nvidia": {"found": False, "why": why}, "raw": text}
    gpus, driver = parse_gpus(done.stdout or "", fields)
    if not gpus:
        why = "nvidia-smi listed no cards."
    else:
        why = f"nvidia-smi listed {len(gpus)} card{'s' if len(gpus) > 1 else ''}."
        if fields is FIELDS_WITHOUT_CAPABILITY:
            why += " This driver is too old to report compute capability."
    return {"gpus": gpus, "driver": driver, "nvidia": {"found": bool(gpus), "why": why}, "raw": text}


def disk_free_mb(path: Path | None) -> int | None:
    """Free space on the volume that holds `path`, or on its nearest parent that exists."""
    if path is None:
        return None
    probe = Path(path).resolve()
    while not probe.exists() and probe.parent != probe:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free // 1_000_000
    except OSError:
        return None


def profile(
    data_root: Path | None = None,
    run: Run = subprocess.run,
    platform: str = sys.platform,
    which: Callable[[str], str | None] = shutil.which,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    """What a plan needs to know about this machine:

        {"os", "gpus": [{"index", "vendor", "name", "capability", "vram_total_mb", "vram_free_mb"}],
         "driver", "nvidia": {"found", "why"}, "disk_free_mb", "raw"}

    `raw` is nvidia-smi's own output, kept for the runtime report."""
    nvidia = read_nvidia(run, platform, which, env)
    return {"os": os_name(platform), **nvidia, "disk_free_mb": disk_free_mb(data_root)}
