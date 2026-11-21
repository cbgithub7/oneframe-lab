"""Three small grey PNGs for the page's e2e test, 1, 2 and 3 pixels square, with no library."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from oneframe.node import Context


def _png(path: Path, side: int, grey: int) -> None:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + bytes([grey]) * side for _ in range(side))
    header = struct.pack(">IIBBBBB", side, side, 8, 0, 0, 0, 0)  # 8-bit greyscale
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def run(ctx: Context) -> None:
    for side in (1, 2, 3):
        path = ctx.path(f"image-{side}.png")
        _png(path, side, 60 * side)
        ctx.output_item("images", path, meta={"width": side, "height": side})
