"""The devices of v1, named once (spec 019): an NVIDIA card or the processor.

The engine branches on them (the fit, the executors, the cap), so they are code, not data; node
code, which can import nothing of the engine but `oneframe.node`, reads them here too. A manifest's
`devices` and `ctx.device` hold these names. No other module of the engine spells one as a device
(`test_devices_are_named_once`); a composed name, such as `cuda:8.6` in a memory model's `time`,
starts from one of them.
"""

from __future__ import annotations

CPU = "cpu"
CUDA = "cuda"
DEVICES = (CPU, CUDA)
