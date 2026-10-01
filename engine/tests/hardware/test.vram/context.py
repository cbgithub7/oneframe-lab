"""The CUDA context's size, for AC8: the card's used memory before CUDA starts and after a first
allocation and kernel, read with nvidia-smi, less what torch reserved. With lazy loading, starting
CUDA alone creates little; the first kernel is what loads the context. Per-process figures are not
available under Windows' display driver, so the whole card is read."""

from __future__ import annotations

import shutil
import subprocess
from typing import Any

MIB = 1.048576  # MB per MiB


def _used_mb(card: int) -> float | None:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return None
    done = subprocess.run(
        [exe, f"--id={card}", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    try:
        return float(done.stdout.strip().splitlines()[0]) * MIB
    except (ValueError, IndexError):
        return None


def run(ctx: Any) -> dict[str, Any]:
    card = int(ctx.params.get("card", 0))
    before = _used_mb(card)
    import torch  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]

    x = torch.ones(1024, device="cuda")
    (x * 2).sum().item()  # a first kernel
    torch.cuda.synchronize()
    after = _used_mb(card)
    reserved = torch.cuda.memory_reserved() / 1e6
    context = None if before is None or after is None else after - before - reserved
    return {"used_before_mb": before, "used_after_mb": after, "reserved_mb": reserved, "context_mb": context}
