"""The estimate: what a run needs on one device, in MB, from a node's memory model, its values and
its inputs' `meta`, corrected by what this machine has learned about the node."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from oneframe.memory.model import PRECISION_PARAM, Formula, MemoryModel
from oneframe.reading import number

# -- the machine and what it learned, as an estimate sees them -----------------------------------


@dataclass(frozen=True)
class Target:
    """What a node's runtime can use: the card its build was planned for (None: no card, as for an
    engine node or a processor build), and the lock hash its learning is kept under."""

    card: int | None = None
    lock: str | None = None


@dataclass(frozen=True)
class Device:
    type: str  # "cuda" or "cpu"
    kind: str  # what learning is kept under: "cpu", "cuda:<capability>" or "cuda:unknown"
    capability: str | None
    free: float | None
    total: float | None
    margin: float
    card: int | None = None

    @property
    def budget(self) -> float | None:
        return None if self.free is None else self.free - self.margin


@dataclass(frozen=True)
class Learned:
    """What this machine learned about one node: a correction per kind of device, and the measured
    need and seconds per (kind of device, settings hash)."""

    corrections: Mapping[str, float] = field(default_factory=dict)
    peaks: Mapping[tuple[str, str], float] = field(default_factory=dict)
    seconds: Mapping[tuple[str, str], float] = field(default_factory=dict)


def settings_hash(values: Mapping[str, Any], inputs: Mapping[str, Mapping[str, Any] | None]) -> str:
    """The values, the precision among them, and every input's size: a peak measured at 512 × 512
    is never used for 4096 × 4096."""
    sizes = {
        port: {k: v for k, v in sorted(meta.items()) if number(v)} if meta is not None else None
        for port, meta in sorted(inputs.items())
    }
    text = json.dumps({"values": dict(values), "inputs": sizes}, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# -- the estimate --------------------------------------------------------------------------------

Inputs = Mapping[str, Mapping[str, Any] | None]  # port -> its meta; None when not connected


@dataclass(frozen=True)
class Need:
    """What a run needs, in unrounded MB. `device` is on the card, or in system memory on the
    processor; `system` is a card run's need in system memory (on the processor it is `device`)."""

    device: float | None
    system: float | None
    working: float | None  # the working estimate before any correction
    basis: str  # "known", "corrected", "measured" or "unknown"
    why: str = ""


def weights_mb(model: MemoryModel, values: Mapping[str, Any]) -> float | None:
    """The weights' size at these values' precision and checkpoint, or None if not known."""
    return _weights(model, values)


def _weights(model: MemoryModel, values: Mapping[str, Any]) -> float | None:
    checkpoint = str(values.get(model.weights_by, "")) if model.weights_by else ""
    return model.weights.get(checkpoint, {}).get(str(values.get(PRECISION_PARAM)))


def _factor(
    model: MemoryModel, name: str, values: Mapping[str, Any], inputs: Inputs
) -> tuple[float | None, str]:
    precision = model.precisions.get(str(values.get(PRECISION_PARAM)))
    if name == "bytes":
        return (precision.bytes, "") if precision else (None, "no precision")
    if name == "weights":
        size = _weights(model, values)
        return size, "" if size is not None else "the weights' size at this precision is not known"
    port, dot, meta_field = name.partition(".")
    if dot:
        if port not in inputs:
            return None, f"the size of input {port} is not known"
        meta = inputs[port]
        if meta is None:
            return 0.0, ""  # an optional input that is not connected
        if meta_field == "pixels":
            width, height = meta.get("width"), meta.get("height")
            if number(width) and number(height):
                return float(width) * float(height), ""
            return None, f"input {port} does not say its width and height"
        value = meta.get(meta_field)
        return (float(value), "") if number(value) else (None, f"input {port} does not say its {meta_field}")
    value = values.get(name)
    if isinstance(value, bool):
        return float(value), ""
    if name in model.factors:
        return model.factors[name].get(str(value)), ""
    return (float(value), "") if number(value) else (None, f"{name} is not a number")


def evaluate(
    formula: Formula, model: MemoryModel, values: Mapping[str, Any], inputs: Inputs
) -> tuple[float | None, str]:
    """A formula's MB, never below 0, or None with why it is not known."""
    if formula.mb is None:
        return None, f"not measured ({formula.source})"
    total = formula.mb
    for term in formula.terms:
        product = term.coef
        for name in term.of:
            value, why = _factor(model, name, values, inputs)
            if value is None:
                return None, why
            product *= value
        total += product
    return max(total, 0.0), ""


def estimate(
    model: MemoryModel,
    values: Mapping[str, Any],
    inputs: Inputs,
    device: Device,
    learned: Learned,
) -> Need:
    correction = learned.corrections.get(device.kind, 1.0)
    working, why = evaluate(model.working, model, values, inputs)
    system, system_why = evaluate(model.system, model, values, inputs)
    if device.type == "cpu":
        weights = _weights(model, values)
        need = (
            None if working is None or weights is None else max(weights + working * correction, system or 0.0)
        )
        if need is not None and system is None:
            need, why = None, system_why
        if need is None and why == "":
            why = "the weights' size at this precision is not known"
    else:
        on_device, on_why = evaluate(model.weights_on_device, model, values, inputs)
        outside, out_why = evaluate(model.outside_torch, model, values, inputs)
        need = None
        if working is not None and on_device is not None and outside is not None:
            need = on_device + working * correction + outside
        else:
            why = why or on_why or out_why
    if need is None:
        peak = learned.peaks.get((device.kind, settings_hash(values, inputs)))
        if peak is not None:
            return Need(peak, peak if device.type == "cpu" else system, working, "measured", why)
        return Need(None, system, working, "unknown", why)
    basis = "corrected" if correction != 1.0 else "known"
    return Need(need, need if device.type == "cpu" else system, working, basis)
