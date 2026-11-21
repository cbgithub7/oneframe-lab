"""Clicks a person placed, as a value on a port (spec 020): what the param holds, written as the
Clicks file a node that takes clicks reads.

It states no trust: a click is neither seen in the photo nor a model's guess, so what is made from
it takes its trust from the photo and the model.
"""

from __future__ import annotations

import json
from typing import Any

from oneframe.node import Context


def run(ctx: Context) -> dict[str, Any]:
    clicks = ctx.params["clicks"]
    path = ctx.path("clicks.json")
    path.write_text(json.dumps(clicks, indent=2), encoding="utf-8")
    meta = {"width": clicks["width"], "height": clicks["height"]}
    ctx.output("clicks", path, meta=meta)
    return {"points": len(clicks["points"]), "boxes": len(clicks["boxes"])}
