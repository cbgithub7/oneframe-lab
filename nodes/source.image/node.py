"""Load a photo: apply its EXIF orientation, keep alpha if it has one, and note its focal length.

The 35 mm equivalent focal length is the one camera value nearly every phone records. Converted to
pixels it is a usable starting guess for a depth model that accepts intrinsics.
"""

from __future__ import annotations

from typing import Any

from PIL import ExifTags, Image, ImageOps

FOCAL_35MM = next(k for k, v in ExifTags.TAGS.items() if v == "FocalLengthIn35mmFilm")


def run(ctx: Any) -> dict[str, Any]:
    with Image.open(ctx.params["path"]) as src:
        exif = src.getexif()
        image = ImageOps.exif_transpose(src)
        alpha = "A" in image.getbands()
        image = image.convert("RGBA" if alpha else "RGB")
    meta: dict[str, Any] = {"width": image.width, "height": image.height, "source": ctx.params["path"]}
    focal_35 = exif.get_ifd(ExifTags.IFD.Exif).get(FOCAL_35MM) or exif.get(FOCAL_35MM)
    if focal_35:
        # 35 mm film is 36 mm wide; scale by the long side, which is what the equivalent refers to.
        meta["focal_px_exif"] = round(float(focal_35) / 36.0 * max(image.width, image.height), 2)
    path = ctx.path("image.png")
    image.save(path)
    ctx.output("image", path, facets={"alpha": "straight" if alpha else "none"}, meta=meta)
    return {"width": image.width, "height": image.height}
