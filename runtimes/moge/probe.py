"""What the runtime report runs inside the moge runtime: which torch, whether it sees the card, and
that MoGe-2's and MoGe-3's code, pinned sources, import with the stand-in for FlexGEMM."""

from __future__ import annotations

from typing import Any

from oneframe.node import Context


def run(ctx: Context) -> dict[str, Any]:
    import cv2
    import numpy
    import torch
    from moge.model.v2 import MoGeModel as MoGe2  # the pinned sources, on the path the install wrote
    from moge.model.v3 import MoGeModel as MoGe3  # imports flex_gemm: the stand-in

    return {
        "torch": torch.__version__,
        "cuda": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "numpy": numpy.__version__,
        "opencv": cv2.__version__,
        "moge": [MoGe2.__name__, MoGe3.__name__],
    }
