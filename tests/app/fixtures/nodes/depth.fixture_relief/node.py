"""A made-up depth for the page's e2e test: two metres away, nearer where the photo is brighter."""

from __future__ import annotations

from typing import Any

import numpy as np
from PIL import Image

from oneframe.node import Context


def run(ctx: Context) -> dict[str, Any]:
    with Image.open(ctx.inputs["image"].path) as src:
        grey = np.asarray(src.convert("L"), dtype=np.float32) / 255.0
    h, w = grey.shape
    depth = (2.0 - float(ctx.params["relief"]) * grey).astype(np.float32)
    f = float(max(h, w))
    u, v = np.meshgrid(np.arange(w, dtype=np.float32) + 0.5, np.arange(h, dtype=np.float32) + 0.5)
    points = np.stack([(u - w / 2) * depth / f, (v - h / 2) * depth / f, depth], axis=-1).astype(np.float32)
    valid = np.ones((h, w), dtype=bool)
    meta = {"width": w, "height": h}
    path = ctx.path("points.npz")
    np.savez_compressed(path, points=points, valid=valid)
    ctx.output("points", path, meta=meta)
    path = ctx.path("depth.npz")
    np.savez_compressed(path, depth=depth, valid=valid)
    ctx.output("depth", path, meta=meta)
    return meta
