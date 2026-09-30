"""What the hardware report runs inside the torch runtime: which torch, whether it sees the card,
and how long one large matrix product takes there."""

from __future__ import annotations

import time
from typing import Any


def run(ctx: Any) -> dict[str, Any]:
    import torch  # type: ignore[import-not-found]

    found: dict[str, Any] = {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
    }
    device = "cuda" if found["cuda_available"] else "cpu"
    if device == "cuda":
        found["capability"] = list(torch.cuda.get_device_capability(0))
    a = torch.randn(4096, 4096, device=device)
    b = torch.randn(4096, 4096, device=device)
    (a @ b).sum().item()  # warm up: the first product pays for kernel loading
    if device == "cuda":
        torch.cuda.synchronize()
    started = time.perf_counter()
    (a @ b).sum().item()
    if device == "cuda":
        torch.cuda.synchronize()
    found["device"] = device
    found["matmul_4096_seconds"] = round(time.perf_counter() - started, 4)
    return found
