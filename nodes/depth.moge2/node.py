"""Microsoft's MoGe-2 (ViT-L, with normals): a metric point map, its depth, the valid pixels, the
camera and normals from one photo (spec 005).

`load` builds the network from the runtime's pinned code with the config its checkpoint holds, and
loads the store's file into it; a checkpoint that leaves any of the network's tensors unloaded is
refused, since those would keep their random values and give a wrong result that looks like a
prediction. `run` is upstream's `infer` at the run's `resolution_level`; at fp16 it is upstream's
autocast over fp32 weights. A pixel the model masks is invalid in every output.
"""

from __future__ import annotations

import json
from typing import Any

from oneframe.node import Context, LoadContext

TITLE = "MoGe-2"
# What this model's code is given and its outputs say, beside what both MoGe nodes share: what
# replaces the config its checkpoint holds, what `infer` is given besides the photo and the
# settings, and what each output's meta says besides its size.
CONFIG: dict[str, Any] = {}
INFER: dict[str, Any] = {}
META: dict[str, Any] = {}


def load(ctx: LoadContext) -> Any:
    import torch
    from moge.model.v2 import MoGeModel

    ctx.stage("load", f"Loading {TITLE}")
    checkpoint = torch.load(ctx.file("weights"), map_location="cpu", weights_only=True)
    model = MoGeModel(**{**checkpoint["model_config"], **CONFIG})
    missing, _ = model.load_state_dict(checkpoint["model"], strict=False)
    del checkpoint
    if missing:
        ctx.fail(
            f"The checkpoint leaves {len(missing)} of {TITLE}'s tensors unloaded, {missing[0]} among them."
        )
    return model.to(ctx.device).eval()


def run(ctx: Context, model: Any) -> dict[str, Any]:
    import numpy as np
    import torch
    from PIL import Image

    with Image.open(ctx.inputs["image"].path) as image:
        rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    level = int(ctx.params["resolution_level"])
    pixels = torch.from_numpy(rgb).to(model.device).permute(2, 0, 1).float() / 255
    ctx.stage("infer", f"Estimating geometry at resolution level {level}")
    with torch.inference_mode():
        out = model.infer(pixels, resolution_level=level, use_fp16=ctx.precision == "fp16", **INFER)
    lacking = sorted({"points", "depth", "mask", "intrinsics", "normal"} - out.keys())
    if lacking:
        ctx.fail(f"{TITLE} gave no {', '.join(lacking)}.")
    found = {name: value.cpu().numpy() for name, value in out.items()}

    depth = found["depth"].astype(np.float32)
    points = found["points"].astype(np.float32)
    valid = found["mask"].astype(bool) & np.isfinite(depth) & (depth > 0) & np.isfinite(points).all(-1)
    k = found["intrinsics"]  # normalised: x by the width, y by the height
    camera = {
        "fx": float(k[0, 0]) * width,
        "fy": float(k[1, 1]) * height,
        "cx": float(k[0, 2]) * width,
        "cy": float(k[1, 2]) * height,
        "width": width,
        "height": height,
    }
    meta = {"width": width, "height": height, **META}

    path = ctx.path("points.npz")
    np.savez_compressed(path, points=np.where(valid[..., None], points, 0).astype(np.float32), valid=valid)
    ctx.output("points", path, meta=meta)
    path = ctx.path("depth.npz")
    np.savez_compressed(path, depth=np.where(valid, depth, 0).astype(np.float32), valid=valid)
    ctx.output("depth", path, meta=meta)
    path = ctx.path("mask.png")
    Image.fromarray(valid.astype(np.uint8) * 255).save(path)
    ctx.output("mask", path, meta=meta)
    path = ctx.path("camera.json")
    path.write_text(json.dumps(camera, indent=2), encoding="utf-8")
    ctx.output("camera", path, meta=meta)
    path = ctx.path("normals.npz")
    normals = np.where(valid[..., None], found["normal"].astype(np.float32), 0).astype(np.float32)
    np.savez_compressed(path, normals=normals)
    ctx.output("normals", path, meta=meta)
    return {
        "focal_px": round(camera["fx"], 2),
        "resolution_level": level,
        "valid_fraction": round(float(valid.mean()), 4),
    }
