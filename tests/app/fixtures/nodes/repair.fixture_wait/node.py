"""Waits for the page's e2e test, until Stop is pressed or its seconds are up."""

from __future__ import annotations

import shutil
import time
from typing import Any

from oneframe.node import Context


def run(ctx: Context) -> dict[str, Any]:
    seconds = float(ctx.params["seconds"])
    ctx.stage("wait", "Waiting for Stop")
    start = time.monotonic()
    while (waited := time.monotonic() - start) < seconds:
        ctx.check_stop()
        ctx.progress(round(waited, 1), seconds, "seconds waited")
        time.sleep(0.05)
    path = ctx.path("image.png")
    shutil.copyfile(ctx.inputs["image"].path, path)
    ctx.output("image", path, facets=ctx.inputs["image"].facets, meta=ctx.inputs["image"].meta)
    return {"waited": seconds}
