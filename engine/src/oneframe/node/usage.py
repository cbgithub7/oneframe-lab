"""What a run uses: this process's resident memory, torch's peaks on the card, what a failed way
left, and the ceiling on torch's allocator.

torch is imported only by the ceiling, in a child about to run on a card; everywhere else it is
read only when node code has already loaded it.
"""

from __future__ import annotations

import gc
import os
import sys
from pathlib import Path
from typing import Any

from oneframe.node import Emit

RESERVE_MB = 256  # kept free under the cap: comfy-aimdo's default headroom (spec 002, research.md)


def cap_allocator(job: dict[str, Any], say: Emit) -> float | None:
    """Cap torch's allocator on the card, before any node code runs, and return the cap in MB.

    On Windows an overrun would spill into system memory and freeze the desktop; with the cap it is
    an out-of-memory error the engine answers with a smaller fit. The cap is the smaller of the
    fit's budget and the free memory measured once the CUDA context exists (less a small reserve),
    so memory another program took since the fit is not counted twice, less what the node
    allocates outside torch. In "tried anyway" and with the fit off the job carries no budget, and
    only the free memory counts. A runtime without torch runs uncapped, and its `ceiling` event
    says so."""
    if not str(job.get("device") or "").startswith("cuda"):
        return None
    budget = job.get("vram_cap_mb")
    outside = float(job.get("outside_torch_mb") or 0)
    try:
        import torch  # type: ignore[import-not-found]
    except ImportError:
        why = "torch is not in this runtime, so nothing was capped"
        say({"event": "ceiling", "applied": False, "budget_mb": budget, "why": why})
        return None
    if not torch.cuda.is_available():
        why = "torch sees no card, so nothing was capped"
        say({"event": "ceiling", "applied": False, "budget_mb": budget, "why": why})
        return None
    torch.cuda.init()
    free, total = (n / 1e6 for n in torch.cuda.mem_get_info())
    cap = free - RESERVE_MB
    if budget is not None:
        cap = min(float(budget), cap)
    cap -= outside
    torch.cuda.set_per_process_memory_fraction(max(0.0, min(1.0, cap / total)))
    say(
        {
            "event": "ceiling",
            "applied": True,
            "budget_mb": None if budget is None else round(float(budget)),
            "free_mb": round(free),
            "reserve_mb": RESERVE_MB,
            "outside_torch_mb": round(outside),
            "vram_cap_mb": round(cap),
            "vram_total_mb": round(total),
        }
    )
    return cap


def peak_vram_mb(device: str, reserved: bool = False) -> float | None:
    """Torch's peak on the card, or None when torch was never loaded or the run was on the CPU
    (asking would start a CUDA context on a card the node was not using)."""
    torch = sys.modules.get("torch")
    if torch is None or not device.startswith("cuda"):
        return None
    try:
        if torch.cuda.is_available():
            peak = torch.cuda.max_memory_reserved() if reserved else torch.cuda.max_memory_allocated()
            return round(peak / 1e6, 1)
    except Exception:
        pass
    return None


def resident_mb() -> tuple[float | None, float | None]:
    """This process's resident memory now and at its peak, in MB, from the standard library:
    /proc and getrusage on Linux, GetProcessMemoryInfo on Windows. None where it cannot be read."""
    if sys.platform == "win32":
        return _windows_resident_mb()
    now: float | None = None
    peak: float | None = None
    try:
        with Path("/proc/self/statm").open(encoding="utf-8") as handle:
            now = int(handle.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except (OSError, ValueError, IndexError):
        now = None
    try:
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        peak = maxrss / 1e6 if sys.platform == "darwin" else maxrss * 1024 / 1e6  # bytes on macOS
    except (ImportError, OSError):
        peak = None
    return now, peak


def _windows_resident_mb() -> tuple[float | None, float | None]:
    if sys.platform != "win32":
        return None, None
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        )

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        return None, None
    return counters.WorkingSetSize / 1e6, counters.PeakWorkingSetSize / 1e6


def peaks(device: str, ram_start: float | None, reserved_floor: float | None = None) -> dict[str, Any]:
    """The peaks a `done` or `error` event carries: torch's on the card (allocated and reserved,
    the reserved one never below `reserved_floor`, the peak of a ctx.fallbacks way that ran out),
    and the growth of this process's resident memory since `ram_start`."""
    reserved = peak_vram_mb(device, reserved=True)
    if reserved_floor is not None:
        reserved = max(reserved or 0.0, reserved_floor)
    _now, peak = resident_mb()
    ram = None if peak is None or ram_start is None else round(max(peak - ram_start, 0.0), 1)
    return {"peak_vram_mb": peak_vram_mb(device), "peak_reserved_mb": reserved, "peak_ram_mb": ram}


def release(device: str) -> None:
    """Free what a failed way left: traceback cycles can keep tensors alive until collected, and
    torch keeps freed blocks cached until asked to give them back. Then start the peak again."""
    gc.collect()
    torch = sys.modules.get("torch")
    if torch is None or not device.startswith("cuda"):
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
            torch.cuda.reset_peak_memory_stats()
    except Exception:
        pass
