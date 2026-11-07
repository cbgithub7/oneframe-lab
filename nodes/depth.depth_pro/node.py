"""Apple's Depth Pro: metric z-depth and a focal length from one photo (spec 005).

`load` builds the network from the runtime's pinned code on torch's meta device, so nothing is
allocated or initialised, then takes its tensors from the store's file as they are (`assign`), casts
them to the run's precision on the processor and only then moves them to the device: an fp16 load
never holds fp32 weights on the card. `run` hands the photo at its own size to upstream's `infer`,
which resizes it to the network's fixed 1536 px and the depth back.

A connected camera's focal length is used instead of the estimate, and that camera, scaled to the
photo, is the one handed on; otherwise the estimate is, as a pinhole camera centred on the photo.
"""

from __future__ import annotations

import json
from typing import Any

from oneframe.node import Context, LoadContext

DTYPES = {"fp32": "float32", "fp16": "float16"}


def load(ctx: LoadContext) -> Any:
    import dataclasses

    import torch
    from depth_pro.depth_pro import DEFAULT_MONODEPTH_CONFIG_DICT, create_model_and_transforms

    ctx.stage("load", "Loading Depth Pro")
    config = dataclasses.replace(DEFAULT_MONODEPTH_CONFIG_DICT, checkpoint_uri=None)  # no fetch, no load
    meta = torch.device("meta")
    with meta:
        model, _ = create_model_and_transforms(config, device=meta)
    state = torch.load(ctx.file("weights"), map_location="cpu", weights_only=True)
    model.load_state_dict(state, strict=True, assign=True)
    del state  # the model holds the only reference: a cast frees each tensor it replaces
    model = model.to(dtype=getattr(torch, DTYPES[ctx.precision]))
    return model.to(ctx.device).eval()


def run(ctx: Context, model: Any) -> dict[str, Any]:
    import numpy as np
    import torch
    from PIL import Image

    with Image.open(ctx.inputs["image"].path) as image:
        rgb = np.asarray(image.convert("RGB"))
    height, width = rgb.shape[:2]
    given = ctx.inputs.get("camera")
    camera = None
    if given is not None:
        camera = scaled(ctx, json.loads(given.path.read_text(encoding="utf-8")), width, height)
    source = "estimated" if given is None else given.facets["source"]

    weights = next(model.parameters())
    pixels = torch.from_numpy(rgb).to(weights.device).permute(2, 0, 1).float()
    pixels = ((pixels / 255 - 0.5) / 0.5).to(weights.dtype)  # upstream's transform: [-1, 1]
    focal = None if camera is None else torch.tensor(float(camera["fx"]), device=weights.device)
    ctx.stage("infer", f"Estimating depth at {model.img_size} px")
    with torch.inference_mode():
        out = model.infer(pixels, f_px=focal)
    depth = out["depth"].float().cpu().numpy()
    if camera is None:
        fx = float(out["focallength_px"])
        camera = {"fx": fx, "fy": fx, "cx": width / 2, "cy": height / 2, "width": width, "height": height}

    valid = np.isfinite(depth) & (depth > 0)
    size = {"width": width, "height": height}
    path = ctx.path("depth.npz")
    np.savez_compressed(path, depth=np.where(valid, depth, 0).astype(np.float32), valid=valid)
    ctx.output("depth", path, meta=size)
    path = ctx.path("camera.json")
    path.write_text(json.dumps(camera, indent=2), encoding="utf-8")
    ctx.output("camera", path, facets={"source": source}, meta=size)
    return {
        "focal_px": round(float(camera["fx"]), 2),
        "network_px": int(model.img_size),
        "valid_fraction": round(float(valid.mean()), 4),
    }


def scaled(ctx: Context, camera: dict[str, Any], width: int, height: int) -> dict[str, Any]:
    """The camera at the photo's size: a camera for the same photo at another size scales with it.
    One of another shape is not this photo's."""
    try:
        sx, sy = width / float(camera["width"]), height / float(camera["height"])
        fx, fy, cx, cy = (float(camera[k]) for k in ("fx", "fy", "cx", "cy"))
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as exc:
        ctx.fail(f"The camera does not say its fx, fy, cx, cy, width and height in pixels ({exc!r}).")
    if abs(sx - sy) > 0.01 * sx:
        ctx.fail(
            f"The camera is for a {camera['width']} × {camera['height']} image, which is not this "
            f"photo's shape ({width} × {height})."
        )
    found = dict(camera)  # a pose, if it has one, stays
    found.update(fx=fx * sx, fy=fy * sy, cx=cx * sx, cy=cy * sy, width=width, height=height)
    return found
