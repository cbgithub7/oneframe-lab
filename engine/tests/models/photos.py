"""The photo spec 005's CI and benches read, made from the owner's original.

    uv run --project engine --frozen python engine/tests/models/photos.py <original>

It writes `photo-full.jpg`, `photo-1536.jpg`, `photo-768.jpg` and `photo-512.jpg` beside this file:
upright (the EXIF orientation applied), RGB, the long side at that size (the full one as taken, and
none made larger), and with no metadata, since a phone's photo records where it was taken. The
original is only read. The files are committed; nothing else reads the original.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageOps

from oneframe import files

HERE = Path(__file__).resolve().parent
SIZES: dict[str, int | None] = {"full": None, "1536": 1536, "768": 768, "512": 512}
QUALITY = 92  # JPEG: a full-size PNG would be tens of MB in git


def upright_pixels(original: Path) -> Image.Image:
    """The photo as it is seen, as a new image holding its pixels alone: no EXIF, ICC profile, XMP
    or comment comes along."""
    with Image.open(original) as image:
        seen = ImageOps.exif_transpose(image).convert("RGB")
    clean = Image.new("RGB", seen.size)
    clean.paste(seen)
    return clean


def sized(image: Image.Image, side: int | None) -> Image.Image:
    """The image with its long side at `side`, or as it is when that would enlarge it."""
    if side is None or max(image.size) <= side:
        return image
    scale = side / max(image.size)
    width, height = (max(1, round(n * scale)) for n in image.size)
    return image.resize((width, height), Image.Resampling.LANCZOS)


def write(original: Path, out_dir: Path = HERE) -> list[Path]:
    """Write every size of `original` into `out_dir`, each in one step; return the paths."""
    image = upright_pixels(original)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, side in SIZES.items():
        path = out_dir / f"photo-{name}.jpg"
        temporary = files.temporary_for(path)
        try:
            sized(image, side).save(temporary, "JPEG", quality=QUALITY, optimize=True)
            files.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        written.append(path)
    return written


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: photos.py <original>", file=sys.stderr)
        return 2
    original = Path(args[0])
    if not original.is_file():
        print(f"no such file: {original}", file=sys.stderr)
        return 1
    for path in write(original):
        with Image.open(path) as image:
            print(f"{path.name}: {image.width} x {image.height}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
