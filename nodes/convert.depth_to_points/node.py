"""z-depth to points: X = (u - cx) z / fx, Y = (v - cy) z / fy, Z = z, per pixel centre.

Depth files are .npz with a `depth` array (H x W, metres); cameras are JSON with `fx`, `fy`, `cx`,
`cy` in pixels of that same image size. Pixels with no depth (0, negative, not finite) are marked
invalid rather than placed at the camera.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np


def run(ctx: Any) -> dict[str, Any]:
    depth = np.load(ctx.inputs["depth"].path)["depth"].astype(np.float32)
    camera = json.loads(Path(ctx.inputs["camera"].path).read_text(encoding="utf-8"))
    h, w = depth.shape
    fx, fy, cx, cy = (float(camera[k]) for k in ("fx", "fy", "cx", "cy"))
    u, v = np.meshgrid(np.arange(w, dtype=np.float32) + 0.5, np.arange(h, dtype=np.float32) + 0.5)
    valid = np.isfinite(depth) & (depth > 0)
    z = np.where(valid, depth, 0.0).astype(np.float32)
    points = np.stack([(u - cx) * z / fx, (v - cy) * z / fy, z], axis=-1).astype(np.float32)
    path = ctx.path("points.npz")
    np.savez_compressed(path, points=points, valid=valid)
    ctx.output("points", path, meta={"width": w, "height": h})
    return {"valid_fraction": round(float(valid.mean()), 4)}
