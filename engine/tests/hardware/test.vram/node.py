"""The AC8 test node: it allocates on its device exactly what its memory model says.

Weights of 1000 MB at fp32 (500 at fp16), and working memory of
200 + 0.00025 × resolution² × bytes + 0.25 × chunk_size MB, held for `hold_s` seconds. So the
estimate is exact by construction, and the measured peak shows what torch and the card add.

`overrun` allocates more than the model says on attempt 1 only, past the cap, so the cap's `oom`,
ctx.fallbacks and the engine's retry can be watched on real hardware.
"""

from __future__ import annotations

import json
import time
from typing import Any

import torch  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]

MB = 1_000_000


def _block(mb: float, device: str, dtype: Any) -> Any:
    count = max(int(mb * MB) // torch.tensor([], dtype=dtype).element_size(), 1)
    block = torch.empty(count, dtype=dtype, device=device)
    block.fill_(1)  # touch it, so the memory is really committed
    return block


def run(ctx: Any) -> dict[str, Any]:
    p = ctx.params
    device = ctx.device
    on_card = device.startswith("cuda")
    fp32 = ctx.precision == "fp32"
    dtype = torch.float32 if fp32 else torch.float16
    size = 4 if fp32 else 2
    weights = _block(1000 if fp32 else 500, device, dtype)
    working_mb = 200 + 0.00025 * p["resolution"] ** 2 * size + 0.25 * p["chunk_size"]
    extra = float(p["overrun"]) if ctx.attempt == 1 else 0.0

    def whole() -> Any:
        return _block(working_mb * (1 + extra), device, dtype)

    def as_modelled() -> Any:
        return _block(working_mb, device, dtype)

    if p["fallbacks"]:
        work = ctx.fallbacks("work", [("whole", whole), ("as modelled", as_modelled)])
    else:
        work = whole()
    if on_card:
        torch.cuda.synchronize()
    time.sleep(float(p["hold_s"]))
    stats = {"weights_mb": weights.numel() * size / MB, "working_mb": work.numel() * size / MB}
    del weights, work
    out = ctx.path("result.json")
    out.write_text(json.dumps(stats), encoding="utf-8")
    ctx.output("text", out)
    return stats
