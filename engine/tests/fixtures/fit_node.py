"""The test node of spec 002's acceptance criteria: its memory model, exact by construction.

Weights of 3000 MB at fp32 and 1500 MB at fp16 and bf16; working memory of
200 + 0.00025 × resolution² × bytes + 0.25 × chunk_size MB; two speed-only changes (chunk size),
two quality changes (fp16, then resolution 512) and one upgrade (chunk size 32768). The plan's test
table is computed from it."""

from __future__ import annotations

import copy
from typing import Any

SOURCE = "test node, exact by construction"

TEST_MODEL: dict[str, Any] = {
    "precisions": {
        "fp32": {"cuda_min_capability": None, "cpu": True},
        "fp16": {"cuda_min_capability": "6.0", "cpu": False},
        "bf16": {"cuda_min_capability": "8.0", "cpu": True},
    },
    "weights": {"fp32": 3000, "fp16": 1500, "bf16": 1500, "source": SOURCE},
    "working": {
        "mb": 200,
        "terms": [
            {"coef": 0.00025, "of": ["resolution", "resolution", "bytes"]},
            {"coef": 0.25, "of": ["chunk_size"]},
        ],
        "source": SOURCE,
    },
    "changes": [
        {"set": {"chunk_size": 2048}, "costs": "speed"},
        {"set": {"chunk_size": 512}, "costs": "speed"},
        {"set": {"precision": "fp16"}, "costs": "quality"},
        {"set": {"resolution": 512}, "costs": "quality"},
    ],
    "upgrades": [{"set": {"chunk_size": 32768}}],
}


PARAMS: dict[str, Any] = {
    "resolution": {"type": "int", "default": 1024, "min": 64, "max": 4096},
    "chunk_size": {"type": "int", "default": 8192, "min": 64, "max": 65536, "affects": "speed"},
}


def model() -> dict[str, Any]:
    """A copy of the model, which a test may change."""
    return copy.deepcopy(TEST_MODEL)
