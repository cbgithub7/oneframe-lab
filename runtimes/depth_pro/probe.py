"""What the runtime report runs inside the depth_pro runtime: which torch, whether it sees the card,
and that Depth Pro's code, a pinned source, imports with the packages listed for it."""

from __future__ import annotations

from typing import Any

from oneframe.node import Context


def run(ctx: Context) -> dict[str, Any]:
    import depth_pro  # the pinned source, on the path the install wrote
    import numpy
    import timm
    import torch

    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "numpy": numpy.__version__,
        "timm": timm.__version__,
        "depth_pro": callable(depth_pro.create_model_and_transforms),
    }
