"""What the runtime report runs inside the tiny runtime: which Python, which packages, which env."""

from __future__ import annotations

import os
import sys
from typing import Any


def run(ctx: Any) -> dict[str, Any]:
    import idna  # type: ignore[import-not-found]
    import tinyext  # type: ignore[import-not-found]

    path = ctx.path("probe.txt")
    path.write_text("ok\n", encoding="utf-8")
    ctx.output("text", path)
    return {
        "python": ".".join(map(str, sys.version_info[:3])),
        "prefix": sys.prefix,
        "idna": idna.__version__,
        "tinyext": tinyext.KIND,
        "env": os.environ.get("ONEFRAME_TINY"),
    }
