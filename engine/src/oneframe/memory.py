"""A node's memory model: what it needs, as data in its manifest, read without loading a model.

The model says how big the node's weights are at each precision (and each checkpoint), where each
precision can run, a formula for its working memory over its own params and its inputs' `meta`,
what it needs in system memory and outside torch, the settings the engine may change to save
memory (speed-only first, then quality), the ones it may use when there is room, and how long a
run takes. Every figure names its source; one nobody has measured is null, never guessed.

A formula is a base plus terms, each a coefficient times a product of factors. It is a list of
numbers to multiply, not a language: nothing is evaluated. See docs/nodes.md for how to write one.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeIs

if TYPE_CHECKING:
    from oneframe.manifest import Param
    from oneframe.ports import PortSpec

PRECISIONS = ("fp32", "fp16", "bf16")
DEFAULT_BYTES = {"fp32": 4, "fp16": 2, "bf16": 2}
COSTS = ("speed", "quality")
PRECISION_PARAM = "precision"
CheckParam = Callable[[str, "Param", Any], "str | None"]
_CAPABILITY = re.compile(r"^\d+\.\d+$")
_FIELD = re.compile(r"^[a-z][a-z0-9_]*$")
_KEYS = {
    "precisions",
    "weights",
    "weights_by",
    "working",
    "weights_on_device",
    "system_mb",
    "outside_torch_mb",
    "factors",
    "changes",
    "upgrades",
    "time",
}


@dataclass(frozen=True)
class Precision:
    name: str
    cuda_min_capability: str | None  # None: runs on any card
    cpu: bool
    bytes: float  # per value of working memory

    def runs_on_card(self, capability: str | None) -> bool:
        """On a card of this capability. A card whose capability is unknown runs only precisions
        that need none."""
        if self.cuda_min_capability is None:
            return True
        if capability is None:
            return False
        return _version(capability) >= _version(self.cuda_min_capability)


def _version(capability: str) -> tuple[int, ...]:
    return tuple(int(p) for p in capability.split("."))


@dataclass(frozen=True)
class Term:
    coef: float
    of: tuple[str, ...]


@dataclass(frozen=True)
class Formula:
    """`mb` plus each term; `mb` None means nobody has measured it, and the estimate is unknown."""

    mb: float | None
    terms: tuple[Term, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class Change:
    set: Mapping[str, Any]
    costs: str = "speed"


@dataclass(frozen=True)
class Time:
    seconds: float | None
    at: Mapping[str, Any]
    source: str


@dataclass(frozen=True)
class MemoryModel:
    precisions: Mapping[str, Precision]
    # checkpoint choice -> precision -> MB, or None where unknown. Without `weights_by` there is one
    # checkpoint, "".
    weights: Mapping[str, Mapping[str, float | None]]
    weights_by: str | None
    weights_source: str
    working: Formula
    weights_on_device: Formula
    system: Formula
    outside_torch: Formula
    factors: Mapping[str, Mapping[str, float]]
    changes: tuple[Change, ...]
    upgrades: tuple[Change, ...]
    time: Mapping[str, Time]
    hash: str = field(compare=False)

    def precision_param(self) -> dict[str, Any]:
        """The `precision` param the engine adds, as a manifest would write it."""
        names = list(self.precisions)
        return {
            "type": "choice",
            "choices": names,
            "default": names[0],
            "label": "Precision",
            "doc": "Added by the engine from the node's memory model.",
        }


def _weights_formula(source: str) -> Formula:
    return Formula(mb=0, terms=(Term(1, ("weights",)),), source=source)


def _number(value: Any) -> TypeIs[int | float]:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _source(row: Mapping[str, Any], where: str, problems: list[str]) -> str:
    source = row.get("source")
    if not isinstance(source, str) or not source.strip():
        problems.append(f"{where} has no source (say where the figure comes from, or why it is null)")
        return ""
    return source


class _Reader:
    """Reads one `memory` object, collecting every problem rather than stopping at the first."""

    def __init__(
        self,
        params: Mapping[str, Param],
        inputs: Mapping[str, PortSpec],
        devices: tuple[str, ...],
        problems: list[str],
        check: CheckParam,
    ) -> None:
        self.check = check
        self.params = params
        self.inputs = inputs
        self.devices = devices
        self.problems = problems
        self.precisions: dict[str, Precision] = {}
        self.factors: dict[str, dict[str, float]] = {}

    def problem(self, text: str) -> None:
        self.problems.append(f"memory.{text}")

    def read_precisions(self, rows: Any) -> None:
        if not isinstance(rows, dict) or not rows:
            self.problem("precisions should list at least one of " + ", ".join(PRECISIONS))
            return
        for name, row in rows.items():
            where = f"precisions.{name}"
            if name not in PRECISIONS:
                self.problem(f"{where}: unknown precision (use {', '.join(PRECISIONS)})")
                continue
            if not isinstance(row, dict) or "cuda_min_capability" not in row or "cpu" not in row:
                self.problem(f"{where} needs cuda_min_capability (null for any card) and cpu (true or false)")
                continue
            capability, cpu, size = (
                row["cuda_min_capability"],
                row["cpu"],
                row.get("bytes", DEFAULT_BYTES[name]),
            )
            ok = True
            if capability is not None and not (isinstance(capability, str) and _CAPABILITY.match(capability)):
                self.problem(f'{where}.cuda_min_capability should read like "8.0", or be null')
                ok = False
            if not isinstance(cpu, bool):
                self.problem(f"{where}.cpu should be true or false")
                ok = False
            if not _number(size) or size <= 0:
                self.problem(f"{where}.bytes should be a positive number")
                ok = False
            if ok:
                unread = any(d not in ("cuda", "cpu") for d in self.devices)  # no rule for them yet
                runs = ("cuda" in self.devices) or (cpu and "cpu" in self.devices) or unread
                if not runs:
                    self.problem(f"{where} runs on none of the node's devices ({', '.join(self.devices)})")
                self.precisions[name] = Precision(name, capability, cpu, float(size))

    def read_weights(self, rows: Any, by: Any) -> tuple[dict[str, dict[str, float | None]], str | None, str]:
        if not isinstance(rows, dict):
            self.problem("weights should be an object of precision to MB, with a source")
            return {}, None, ""
        source = _source(rows, "memory.weights", self.problems)
        tables: dict[str, Any] = {k: v for k, v in rows.items() if k != "source"}
        if by is not None:
            param = self.params.get(by) if isinstance(by, str) else None
            if param is None or param.type != "choice":
                self.problem(f"weights_by {by!r} should name one of the node's choice params")
                return {}, None, source
            for choice in param.choices:
                if choice not in tables:
                    self.problem(f"weights has no sizes for {by} {choice!r}")
            for extra in tables.keys() - set(param.choices):
                self.problem(f"weights.{extra} is not a choice of {by}")
            checkpoints = {c: tables[c] for c in param.choices if c in tables}
        else:
            checkpoints = {"": tables}
        weights: dict[str, dict[str, float | None]] = {}
        for checkpoint, table in checkpoints.items():
            where = f"weights.{checkpoint}" if checkpoint else "weights"
            if not isinstance(table, dict):
                self.problem(f"{where} should be an object of precision to MB")
                continue
            sizes: dict[str, float | None] = {}
            for precision in self.precisions:
                if precision not in table:
                    self.problem(f"{where} has no size for {precision} (write null if it is not known)")
                    continue
                size = table[precision]
                if size is not None and (not _number(size) or size < 0):
                    self.problem(f"{where}.{precision} should be MB, or null")
                    continue
                sizes[precision] = None if size is None else float(size)
            for extra in table.keys() - set(self.precisions):
                self.problem(f"{where}.{extra} is not one of the model's precisions")
            weights[checkpoint] = sizes
        return weights, (by if isinstance(by, str) else None), source

    def read_factors(self, rows: Any) -> None:
        if rows is None:
            return
        if not isinstance(rows, dict):
            self.problem("factors should be an object of choice param to {choice: number}")
            return
        for name, table in rows.items():
            param = self.params.get(name)
            if param is None or param.type != "choice":
                self.problem(f"factors.{name} should name one of the node's choice params")
                continue
            if not isinstance(table, dict) or not all(_number(v) for v in table.values()):
                self.problem(f"factors.{name} should give a number for each choice")
                continue
            missing = [c for c in param.choices if c not in table]
            if missing:
                self.problem(f"factors.{name} has no number for {', '.join(missing)}")
                continue
            self.factors[name] = {c: float(table[c]) for c in param.choices}

    def check_factor(self, name: Any, where: str) -> bool:
        if not isinstance(name, str):
            self.problem(f"{where}: a factor should be a name")
            return False
        if name in ("bytes", "weights"):
            return True
        port, dot, meta = name.partition(".")
        if dot:
            if port not in self.inputs:
                self.problem(f"{where}: {name!r} names no input of this node")
                return False
            if not _FIELD.match(meta):
                self.problem(f"{where}: {name!r} should name a field of {port}'s meta")
                return False
            return True
        param = self.params.get(name)
        if param is None:
            self.problem(f"{where}: {name!r} is neither a param, bytes, weights, nor <input>.<field>")
            return False
        if param.type in ("int", "float", "bool"):
            return True
        if param.type == "choice":
            if name not in self.factors:
                self.problem(f"{where}: the choice param {name!r} needs a number per choice in factors")
                return False
            return True
        self.problem(f"{where}: {name!r} is a {param.type} param, which is not a number")
        return False

    def read_formula(self, row: Any, where: str, *, terms: bool = True) -> Formula | None:
        if row is None:
            return None
        if not isinstance(row, dict):
            self.problem(f"{where} should be an object with mb, terms and a source")
            return None
        source = _source(row, f"memory.{where}", self.problems)
        base = row.get("mb", 0)
        if base is not None and not _number(base):
            self.problem(f"{where}.mb should be a number, or null if it is not known")
            return None
        parsed: list[Term] = []
        rows = row.get("terms") or []
        if rows and not terms:
            self.problem(f"{where} is a number of MB, without terms")
        elif not isinstance(rows, list):
            self.problem(f"{where}.terms should be a list")
        else:
            for i, term in enumerate(rows):
                at = f"{where}.terms[{i}]"
                if not isinstance(term, dict) or not _number(term.get("coef")):
                    self.problem(f"{at} needs a number coef")
                    continue
                factors = term.get("of") or []
                if not isinstance(factors, list):
                    self.problem(f"{at}.of should be a list of names")
                    continue
                if all([self.check_factor(f, at) for f in factors]):  # a list: name every problem
                    parsed.append(Term(float(term["coef"]), tuple(factors)))
        return Formula(None if base is None else float(base), tuple(parsed), source)

    def check_set(self, change: Any, where: str, costs: str) -> Change | None:
        if not isinstance(change, dict) or not isinstance(change.get("set"), dict) or not change["set"]:
            self.problem(f"{where} needs a set of params to change")
            return None
        ok = True
        output_affecting = False
        for name, value in change["set"].items():
            if name == PRECISION_PARAM:
                output_affecting = True
                if value not in self.precisions:
                    self.problem(f"{where} sets precision {value!r}, which the model does not list")
                    ok = False
                elif costs == "speed":
                    advice = "" if where.startswith("upgrades") else ' (mark the change "costs": "quality")'
                    self.problem(f"{where} costs speed but sets precision, which changes the output{advice}")
                    ok = False
                continue
            param = self.params.get(name)
            if param is None:
                self.problem(f"{where} sets {name!r}, which is not a param of this node")
                ok = False
                continue
            if param.type == "file":
                self.problem(f"{where} sets {name!r}, a file; the engine never changes a file")
                ok = False
                continue
            why = self.check(name, param, value)
            if why:
                self.problem(f"{where}: {why}")
                ok = False
            if param.affects == "output":
                output_affecting = True
                if costs == "speed":
                    advice = "" if where.startswith("upgrades") else ', or the change "costs": "quality"'
                    self.problem(
                        f"{where} costs speed but sets {name!r}, which changes the output "
                        f'(mark the param "affects": "speed"{advice})'
                    )
                    ok = False
        if costs == "quality" and not output_affecting and ok:
            self.problem(f'{where} costs quality but sets only speed params; mark it "costs": "speed"')
            ok = False
        return Change(dict(change["set"]), costs) if ok else None

    def read_changes(self, rows: Any) -> tuple[Change, ...]:
        if rows is None:
            return ()
        if not isinstance(rows, list):
            self.problem("changes should be a list")
            return ()
        out: list[Change] = []
        seen_quality = False
        for i, row in enumerate(rows):
            where = f"changes[{i}]"
            costs = row.get("costs") if isinstance(row, dict) else None
            if costs not in COSTS:
                self.problem(f"{where}.costs should be speed or quality")
                continue
            if costs == "speed" and seen_quality:
                self.problem(f"{where} costs speed but comes after a change that costs quality")
            seen_quality = seen_quality or costs == "quality"
            change = self.check_set(row, where, costs)
            if change:
                out.append(change)
        return tuple(out)

    def read_upgrades(self, rows: Any) -> tuple[Change, ...]:
        if rows is None:
            return ()
        if not isinstance(rows, list):
            self.problem("upgrades should be a list")
            return ()
        changes = [self.check_set(row, f"upgrades[{i}]", "speed") for i, row in enumerate(rows)]
        return tuple(c for c in changes if c)

    def read_time(self, rows: Any) -> dict[str, Time]:
        if rows is None:
            return {}
        if not isinstance(rows, dict):
            self.problem("time should be an object of kind of device to {seconds, at, source}")
            return {}
        out: dict[str, Time] = {}
        for kind, row in rows.items():
            where = f"time.{kind}"
            device, _, capability = str(kind).partition(":")
            if device not in self.devices or (capability and not _CAPABILITY.match(capability)):
                self.problem(
                    f"{where}: name one of the node's devices, optionally with a capability (cuda:8.6)"
                )
                continue
            if not isinstance(row, dict):
                self.problem(f"{where} should be {{seconds, at, source}}")
                continue
            source = _source(row, f"memory.{where}", self.problems)
            seconds, at = row.get("seconds"), row.get("at") or {}
            if seconds is not None and (not _number(seconds) or seconds < 0):
                self.problem(f"{where}.seconds should be a number, or null")
                continue
            if not isinstance(at, dict):
                self.problem(f"{where}.at should name the settings it was measured at")
                continue
            unknown = [n for n in at if n != PRECISION_PARAM and n not in self.params]
            if unknown:
                self.problem(f"{where}.at names {', '.join(unknown)}, which are not params of this node")
                continue
            out[kind] = Time(None if seconds is None else float(seconds), dict(at), source)
        return out


def parse(
    raw: Any,
    params: Mapping[str, Param],
    inputs: Mapping[str, PortSpec],
    devices: tuple[str, ...],
    problems: list[str],
    check: CheckParam,
) -> MemoryModel | None:
    """The `memory` object of a manifest, or None with the problems added to `problems`.

    `check` is the manifest's own check of a param's value, so a change is held to the same rules
    as a graph."""
    if not isinstance(raw, dict):
        problems.append("memory should be an object")
        return None
    start = len(problems)
    reader = _Reader(params, inputs, devices, problems, check)
    for key in raw.keys() - _KEYS:
        reader.problem(f"{key} is not part of a memory model")
    reader.read_precisions(raw.get("precisions"))
    reader.read_factors(raw.get("factors"))
    weights, by, weights_source = reader.read_weights(raw.get("weights"), raw.get("weights_by"))
    working = reader.read_formula(raw.get("working"), "working")
    if raw.get("working") is None:
        reader.problem("working is required (write mb null, with a source, if it is not known)")
    on_device = reader.read_formula(raw.get("weights_on_device"), "weights_on_device")
    system = reader.read_formula(raw.get("system_mb"), "system_mb")
    outside = reader.read_formula(raw.get("outside_torch_mb"), "outside_torch_mb", terms=False)
    changes = reader.read_changes(raw.get("changes"))
    upgrades = reader.read_upgrades(raw.get("upgrades"))
    time = reader.read_time(raw.get("time"))
    if len(problems) > start or working is None:
        return None
    return MemoryModel(
        precisions=reader.precisions,
        weights=weights,
        weights_by=by,
        weights_source=weights_source,
        working=working,
        weights_on_device=on_device or _weights_formula(weights_source),
        system=system or _weights_formula(weights_source),
        outside_torch=outside or Formula(0, (), "none declared"),
        factors=reader.factors,
        changes=changes,
        upgrades=upgrades,
        time=time,
        hash=model_hash(raw),
    )


def model_hash(raw: Mapping[str, Any]) -> str:
    """What the learned store keys on: a change to the model starts its learning over."""
    text = json.dumps(raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# -- the settings ------------------------------------------------------------------------------

MARGIN_FLOOR_MB = 1611  # 1.5 GiB, in MB of 10^6 bytes
MARGIN_SHARE = 0.10  # of the device's total, when that is larger
SETTINGS_FILE = "settings.json"
FIT_MODES = ("on", "off")


@dataclass(frozen=True)
class Settings:
    """The person's control over the fit, from `<data>/settings.json`'s `memory` object."""

    margin_mb: Mapping[str, float | None] = field(default_factory=lambda: {"cuda": None, "cpu": None})
    never_reduce_quality: bool = False
    fit: str = "on"
    notes: tuple[str, ...] = ()  # what in the file was not understood, and so left at its default

    def to_json(self) -> dict[str, Any]:
        return {
            "margin_mb": dict(self.margin_mb),
            "never_reduce_quality": self.never_reduce_quality,
            "fit": self.fit,
            **({"notes": list(self.notes)} if self.notes else {}),
        }


def read_settings(data_root: Path | None) -> Settings:
    """The settings, read again at each fit. A missing file is the defaults; a value that is not
    understood is left at its default, and said so in `notes`."""
    if data_root is None:
        return Settings()
    path = Path(data_root) / SETTINGS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        return Settings(notes=(f"{SETTINGS_FILE} could not be read ({exc}); the defaults are used",))
    row = data.get("memory") if isinstance(data, dict) else None
    if row is None:
        return Settings()
    if not isinstance(row, dict):
        return Settings(notes=(f"{SETTINGS_FILE}: memory should be an object; the defaults are used",))
    notes: list[str] = []
    margins: dict[str, float | None] = {"cuda": None, "cpu": None}
    for device, value in (row.get("margin_mb") or {}).items():
        if device not in margins:
            notes.append(f"margin_mb.{device}: only cuda and cpu have a margin")
        elif value is not None and (not _number(value) or value < 0):
            notes.append(f"margin_mb.{device} should be MB, or null for the default")
        else:
            margins[device] = None if value is None else float(value)
    never = row.get("never_reduce_quality", False)
    if not isinstance(never, bool):
        notes.append("never_reduce_quality should be true or false")
        never = False
    mode = row.get("fit", "on")
    if mode not in FIT_MODES:
        notes.append('fit should be "on" or "off"')
        mode = "on"
    return Settings(margins, never, mode, tuple(notes))


def margin(settings: Settings, device_type: str, total_mb: float | None) -> float:
    """The person's margin for this type of device, or the larger of 1.5 GiB and 10% of its total."""
    chosen = settings.margin_mb.get(device_type)
    if chosen is not None:
        return float(chosen)
    return max(MARGIN_FLOOR_MB, MARGIN_SHARE * total_mb) if total_mb else float(MARGIN_FLOOR_MB)


# -- the machine, as a fit sees it ---------------------------------------------------------------


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


def _system_device(machine: Mapping[str, Any], settings: Settings) -> Device:
    system = machine.get("system") or {}
    total = system.get("total_mb")
    return Device("cpu", "cpu", None, system.get("free_mb"), total, margin(settings, "cpu", total))


def _card(machine: Mapping[str, Any], target: Target, settings: Settings) -> Device | None:
    if target.card is None:
        return None
    gpu = next((g for g in machine.get("gpus") or [] if g.get("index") == target.card), None)
    if gpu is None:
        return None
    capability = gpu.get("capability")
    total = gpu.get("vram_total_mb")
    return Device(
        "cuda",
        f"cuda:{capability or 'unknown'}",
        capability,
        gpu.get("vram_free_mb"),
        total,
        margin(settings, "cuda", total),
        target.card,
    )


# -- what this machine has learned, as a fit sees it ---------------------------------------------


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
        port: {k: v for k, v in sorted(meta.items()) if _number(v)} if meta is not None else None
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
            if _number(width) and _number(height):
                return float(width) * float(height), ""
            return None, f"input {port} does not say its width and height"
        value = meta.get(meta_field)
        return (float(value), "") if _number(value) else (None, f"input {port} does not say its {meta_field}")
    value = values.get(name)
    if isinstance(value, bool):
        return float(value), ""
    if name in model.factors:
        return model.factors[name].get(str(value)), ""
    return (float(value), "") if _number(value) else (None, f"{name} is not a number")


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


# -- the fit -------------------------------------------------------------------------------------


@dataclass
class Fit:
    """Where a node runs, at which values, and why. `outcome` is:

    - "fits": the values fit the device's budget;
    - "unknown": the estimate or the budget could not be known, so it runs at the values reached;
    - "unfitted": the node has no memory model, so it runs at its values on its first device;
    - "off": the person turned the fit off;
    - "tried_anyway": nothing fits, but a setting the graph sets kept a change from being made;
    - "memory": nothing fits anywhere; `message` says what would help."""

    outcome: str
    device: str | None = None
    kind: str | None = None
    card: int | None = None
    values: dict[str, Any] = field(default_factory=dict)
    applied: tuple[int, ...] = ()  # the changes made, by index
    upgrades: tuple[int, ...] = ()
    reasons: dict[int, str] = field(default_factory=dict)
    skipped: list[dict[str, Any]] = field(default_factory=list)
    need: Need | None = None
    devices: dict[str, dict[str, Any]] = field(default_factory=dict)
    warning: dict[str, Any] | None = None
    message: str = ""
    notes: list[str] = field(default_factory=list)
    reduced: bool = False  # a change that costs quality was made

    @property
    def budget(self) -> float | None:
        row = self.devices.get(self.device or "")
        return row.get("budget") if row else None

    @property
    def last_change(self) -> int:
        return max(self.applied, default=-1)

    def to_json(self) -> dict[str, Any]:
        def mb(value: Any) -> Any:
            return round(value) if isinstance(value, float) else value

        need = self.need
        return {
            "outcome": self.outcome,
            "device": self.device,
            "kind": self.kind,
            "precision": self.values.get(PRECISION_PARAM),
            "values": dict(self.values),
            "changes": [{"change": i, "why": self.reasons.get(i, "")} for i in self.applied],
            "upgrades": list(self.upgrades),
            "skipped": list(self.skipped),
            "reduced": self.reduced,
            "estimate": need.basis if need else "unknown",
            "needs": {"device": mb(need.device), "system": mb(need.system)} if need else None,
            "devices": {name: {k: mb(v) for k, v in row.items()} for name, row in self.devices.items()},
            "warning": self.warning,
            "message": self.message,
            "notes": list(self.notes),
        }


def can_retry(found: Fit) -> bool:
    """Whether an attempt that ran out of memory may be fitted again. Not without a memory model,
    when tried anyway, or with the fit off: nothing would change."""
    return found.outcome in ("fits", "unknown")


@dataclass
class _Point:
    """One place the walk looks: a device and the values after some changes."""

    device: Device
    values: dict[str, Any]
    applied: tuple[int, ...]
    upgrades: tuple[int, ...] = ()
    need: Need | None = None


def _devices_here(
    devices: tuple[str, ...], machine: Mapping[str, Any], target: Target, settings: Settings
) -> list[Device]:
    """The node's devices that this machine has and its runtime can use, in the manifest's order.
    `mps`, `xpu` and `rocm` are not read yet."""
    here: list[Device] = []
    for name in devices:
        if name == "cuda":
            card = _card(machine, target, settings)
            if card is not None:
                here.append(card)
        elif name == "cpu":
            here.append(_system_device(machine, settings))
    return here


class _Fitter:
    def __init__(
        self,
        model: MemoryModel,
        values: Mapping[str, Any],
        explicit: frozenset[str],
        inputs: Inputs,
        learned: Learned,
        settings: Settings,
        devices: tuple[str, ...],
        here: list[Device],
        system: Device,
    ) -> None:
        self.model = model
        self.values = dict(values)
        self.explicit = explicit
        self.inputs = inputs
        self.learned = learned
        self.settings = settings
        self.devices = devices
        self.here = here
        self.system = system
        self.rows = {d.type: _row(d) for d in [*here, system]}
        self.notes = list(settings.notes)
        if system.free is None:
            self.notes.append("System memory could not be read, so a card's fit does not check it")
        self.skipped: dict[tuple[str, int], str] = {}
        self.explicit_blocked = False
        self.smallest: dict[str, _Point] = {}  # per type of device, the smallest need seen
        self.precision_skipped: dict[str, set[str]] = {}

    def fresh(self) -> _Fitter:
        return _Fitter(
            self.model,
            self.values,
            self.explicit,
            self.inputs,
            self.learned,
            self.settings,
            self.devices,
            self.here,
            self.system,
        )

    # -- looking at one point --

    def runs_on(self, device: Device, precision: str) -> bool:
        rule = self.model.precisions.get(precision)
        if rule is None:
            return False
        return rule.cpu if device.type == "cpu" else rule.runs_on_card(device.capability)

    def blocked(self, change: Change, device: Device) -> str | None:
        """Why a change cannot be made on this device, or None."""
        set_by_graph = sorted(set(change.set) & self.explicit)
        if set_by_graph:
            self.explicit_blocked = True
            return f"the graph sets {', '.join(set_by_graph)}"
        precision = change.set.get(PRECISION_PARAM)
        if precision is not None and not self.runs_on(device, str(precision)):
            self.precision_skipped.setdefault(device.type, set()).add(str(precision))
            return f"{precision} cannot run on {'the processor' if device.type == 'cpu' else device.kind}"
        return None

    def reachable(self, device: Device) -> bool:
        """Whether the node can run on this device: at the values' precision, or at one a change
        the fit may make sets (precision changes cost quality, so not with never_reduce_quality)."""
        if self.runs_on(device, str(self.values.get(PRECISION_PARAM))):
            return True
        if self.settings.never_reduce_quality or PRECISION_PARAM in self.explicit:
            return False
        return any(
            self.runs_on(device, str(change.set[PRECISION_PARAM]))
            for change in self.model.changes
            if PRECISION_PARAM in change.set and not set(change.set) & self.explicit
        )

    def points(self, device: Device, quality: bool, after: int | None = None) -> list[_Point]:
        """The values, then each change in turn, cumulatively. With `after`, a retry's: only the
        points past that change (-1: past the values), so it never repeats what ran out."""
        out: list[_Point] = []
        values: dict[str, Any] = dict(self.values)
        applied: tuple[int, ...] = ()
        if after is None and self.runs_on(device, str(values.get(PRECISION_PARAM))):
            out.append(_Point(device, dict(values), applied))
        for i, change in enumerate(self.model.changes):
            if change.costs == "quality" and not quality:
                break
            why = self.blocked(change, device)
            if why:
                self.skipped.setdefault(("change", i), why)
                continue
            values.update(change.set)
            applied = (*applied, i)
            runs_here = self.runs_on(device, str(values.get(PRECISION_PARAM)))
            if (after is None or i > after) and runs_here:
                out.append(_Point(device, dict(values), applied))
        return out

    def measure(self, point: _Point) -> _Point:
        point.need = estimate(self.model, point.values, self.inputs, point.device, self.learned)
        need = point.need.device
        if need is not None:
            best = self.smallest.get(point.device.type)
            if best is None or best.need is None or best.need.device is None or need < best.need.device:
                self.smallest[point.device.type] = point
        return point

    def fits(self, point: _Point) -> bool:
        need, device = point.need, point.device
        if need is None or need.device is None or device.budget is None or need.device > device.budget:
            return False
        if device.type == "cuda" and self.system.budget is not None and need.system is not None:
            return need.system <= self.system.budget
        return True

    def unknown(self, point: _Point) -> bool:
        return point.need is None or point.need.device is None or point.device.budget is None

    # -- results --

    def result(self, point: _Point, outcome: str, warning: dict[str, Any] | None = None) -> Fit:
        found = Fit(
            outcome,
            point.device.type,
            point.device.kind,
            point.device.card,
            point.values,
            applied=point.applied,
            upgrades=point.upgrades,
            reasons=self.reasons(point),
            skipped=self.skipped_rows(),
            need=point.need,
            devices=self.rows,
            warning=warning,
            notes=self.notes,
            reduced=any(self.model.changes[i].costs == "quality" for i in point.applied),
        )
        if found.warning is None and self.devices and found.device != self.devices[0]:
            found.warning = self.slow(found)
        return found

    def stop_unknown(self, point: _Point) -> Fit:
        if point.need is not None and point.need.device is None:
            why = f"The memory this needs is not known: {point.need.why}"
        else:
            why = f"The free memory on {point.device.kind} could not be read"
        return self.result(point, "unknown", {"kind": "unknown", "why": why})

    def skipped_rows(self) -> list[dict[str, Any]]:
        return [{what: i, "why": why} for (what, i), why in sorted(self.skipped.items())]

    def reasons(self, point: _Point) -> dict[int, str]:
        out: dict[int, str] = {}
        for i in point.applied:
            change = self.model.changes[i]
            costs = (
                "costs quality: no speed-only change fits"
                if change.costs == "quality"
                else "costs speed only"
            )
            settings = ", ".join(f"{k} {self.values.get(k)} → {v}" for k, v in change.set.items())
            out[i] = f"{settings}; {costs}"
        return out

    def slow(self, found: Fit) -> dict[str, Any]:
        """The warning when the fit leaves the node's first listed device: how long it may take, on
        what basis, and the faster alternative at reduced quality, if there is one."""
        kind = found.kind or ""
        seconds = self.learned.seconds.get((kind, settings_hash(found.values, self.inputs)))
        basis = "measured"
        if seconds is None:
            published = self.model.time.get(kind) or self.model.time.get(found.device or "")
            seconds = published.seconds if published else None
            basis = "published" if seconds is not None else "unknown"
        alternative = None
        first = next((d for d in self.here if d.type == self.devices[0]), None)
        if first is not None and not self.settings.never_reduce_quality and self.reachable(first):
            probe = self.fresh()
            for point in probe.points(first, quality=True):
                if probe.fits(probe.measure(point)):
                    alternative = {
                        "device": first.type,
                        "changes": list(point.applied),
                        "set": {k: v for k, v in point.values.items() if self.values.get(k) != v},
                        "reduced": any(self.model.changes[i].costs == "quality" for i in point.applied),
                    }
                    break
        return {"kind": "slow", "seconds": seconds, "basis": basis, "alternative": alternative}

    def failed(self, message: str) -> Fit:
        return Fit(
            "memory",
            values=self.values,
            skipped=self.skipped_rows(),
            devices=self.rows,
            message=message,
            notes=self.notes,
        )

    # -- the walk --

    def run(self, after: Fit | None) -> Fit:
        precision = str(self.values.get(PRECISION_PARAM))
        usable = [d for d in self.here if self.reachable(d)]
        # The first device the values themselves run on: where the fit off, the upgrades and
        # "tried anyway" run. A device only a precision change makes usable comes later.
        first = next((d for d in usable if self.runs_on(d, precision)), None)
        if not usable or (first is None and self.settings.fit == "off"):
            return self.failed(_no_device(self.devices, self.model.precisions.get(precision)))
        if self.settings.fit == "off" and first is not None:
            return self.result(self.measure(_Point(first, dict(self.values), ())), "off")

        if after is None and first is not None:
            base = self.measure(_Point(first, dict(self.values), ()))
            if self.unknown(base):
                return self.stop_unknown(base)
            upgraded = self.upgrade(first)
            if upgraded is not None:
                return self.result(upgraded, "fits")

        # Where a retry starts: past the change the failed attempt ended at, on its device. An
        # attempt that used upgrades starts again from the values, without them.
        start, past = 0, None
        if after is not None and not after.upgrades:
            start = next((i for i, d in enumerate(usable) if d.type == after.device), 0)
            past = after.last_change
        next_only = after is not None and after.outcome == "unknown" and not after.upgrades

        for quality in [False] if self.settings.never_reduce_quality else [False, True]:
            for index in range(start, len(usable)):
                points = self.points(usable[index], quality, past if index == start else None)
                if next_only and index == start:
                    points = points[:1]
                for point in points:
                    self.measure(point)
                    if self.unknown(point):
                        return self.stop_unknown(point)
                    if self.fits(point):
                        return self.result(point, "fits")

        if after is not None:
            return self.failed("Nothing smaller than the attempt that ran out of memory is left to try.")
        if self.explicit_blocked:
            for device in [d for d in (first, *usable) if d is not None]:
                allowed = self.points(device, not self.settings.never_reduce_quality)
                if allowed:
                    point = self.measure(allowed[-1])
                    why = "Nothing fits in the free memory, and the graph sets what the engine would change"
                    return self.result(point, "tried_anyway", {"kind": "tried_anyway", "why": why})
        return self.failed(self.memory_message(usable))

    def upgrade(self, first: Device) -> _Point | None:
        """The most upgrades, in order, that fit on the first device; an upgrade that sets a param
        the graph sets is skipped."""
        best: _Point | None = None
        values: dict[str, Any] = dict(self.values)
        used: tuple[int, ...] = ()
        for i, upgrade in enumerate(self.model.upgrades):
            set_by_graph = sorted(set(upgrade.set) & self.explicit)
            if set_by_graph:
                self.skipped.setdefault(("upgrade", i), f"the graph sets {', '.join(set_by_graph)}")
                continue
            values.update(upgrade.set)
            used = (*used, i)
            point = self.measure(_Point(first, dict(values), (), used))
            if not self.unknown(point) and self.fits(point):
                best = point
        return best

    def memory_message(self, usable: list[Device]) -> str:
        parts: list[str] = []
        for device in usable:
            point = self.smallest.get(device.type)
            if point is None or point.need is None or point.need.device is None:
                continue
            need = point.need.device
            system = point.need.system
            free = round(device.free or 0)
            if device.type == "cpu":
                cannot = sorted(self.precision_skipped.get("cpu", ()))
                note = f", {' and '.join(cannot)} cannot run there" if cannot else ""
                parts.append(
                    f"Needs {round(need + device.margin)} MB free in system memory for the processor "
                    f"({round(need)} MB{note}, plus {round(device.margin)} MB); {free} MB are free."
                )
            elif device.budget is not None and need <= device.budget and system is not None:
                parts.append(
                    f"Needs {round(system + self.system.margin)} MB free in system memory to load "
                    f"its weights for the card ({round(system)} MB plus a {round(self.system.margin)} MB "
                    f"margin); {round(self.system.free or 0)} MB are free."
                )
            else:
                parts.append(
                    f"Needs {round(need + device.margin)} MB free on the card ({round(need)} MB at the "
                    f"smallest settings plus a {round(device.margin)} MB margin); {free} MB are free."
                )
        parts.append(
            "Closing other programs may free enough; otherwise it needs a device with that much memory."
        )
        return " ".join(parts)


def _row(device: Device) -> dict[str, Any]:
    return {
        "kind": device.kind,
        "free": device.free,
        "total": device.total,
        "margin": device.margin,
        "budget": device.budget,
    }


def _no_device(devices: tuple[str, ...], rule: Precision | None = None) -> str:
    listed = ", ".join(devices) or "no device"
    unread = [d for d in devices if d not in ("cuda", "cpu")]
    if unread and not any(d in ("cuda", "cpu") for d in devices):
        return f"This node runs on {listed}; the engine does not read {', '.join(unread)} yet."
    if rule is not None:
        card = (
            f"a card of compute {rule.cuda_min_capability} or later" if rule.cuda_min_capability else "a card"
        )
        where = f"{card} or the processor" if rule.cpu else card
        return f"This node runs on {listed}. At {rule.name} it needs {where}, which this machine lacks."
    return f"This node runs on {listed}, and this machine has none of them that its runtime can use."


def fit(
    model: MemoryModel | None,
    values: Mapping[str, Any],
    explicit: frozenset[str] | set[str],
    inputs: Inputs,
    machine: Mapping[str, Any],
    target: Target,
    learned: Learned,
    settings: Settings,
    devices: tuple[str, ...],
    after: Fit | None = None,
) -> Fit:
    """Where a node runs and at which values.

    Pure: the same arguments give the same fit, and no card's name is read. `values` are the step's
    params (with `precision`), `explicit` the ones the graph sets, `inputs` each input's `meta`,
    `devices` the manifest's list, and `after` the fit of an attempt that ran out of memory, which
    the result must be strictly smaller than."""
    system = _system_device(machine, settings)
    here = _devices_here(devices, machine, target, settings)
    if model is None:
        rows = {d.type: _row(d) for d in [*here, system]}
        if not here:
            return Fit("memory", values=dict(values), devices=rows, message=_no_device(devices))
        first = here[0]
        found = Fit("unfitted", first.type, first.kind, first.card, dict(values), devices=rows)
        found.notes = list(settings.notes)
        if devices and first.type != devices[0]:
            found.warning = {"kind": "slow", "seconds": None, "basis": "unknown", "alternative": None}
        return found
    fitter = _Fitter(model, values, frozenset(explicit), inputs, learned, settings, devices, here, system)
    return fitter.run(after)


# -- what each machine learns ----------------------------------------------------------------------

STORE_VERSION = 1
STORE_ENTRIES = 256  # the least recently used entry goes first
RECENT_RATIOS = 8
RECENT_SETTINGS = 16
MIN_WORKING_MB = 64  # a smaller working estimate gives a ratio that is noise
LOWER_AFTER_RUNS = 2  # successful runs that must agree before a correction goes below 1


@dataclass(frozen=True)
class StoreKey:
    """What a node's learning is kept under: a new version, memory model or runtime lock starts it
    over, and each kind of device learns on its own."""

    node: str
    version: str
    model: str  # the memory model's hash; "" without one
    lock: str  # the runtime lock's hash; "" for none

    def text(self, kind: str) -> str:
        return "|".join((self.node, self.version, self.model, self.lock, kind))


class LearnedStore:
    """`<data>/memory/learned.json`: per node and kind of device, the recent working-memory ratios,
    and the measured need and seconds for recent settings. Written to a temporary file and renamed
    into place, so an interrupted write leaves the previous file. Without a data root it lives in
    memory only."""

    def __init__(self, data_root: Path | None) -> None:
        self.path = None if data_root is None else Path(data_root) / "memory" / "learned.json"
        self._data: dict[str, Any] | None = None
        # A run records while nodes.fit and nodes.forget read and clear, from another thread.
        self._lock = threading.RLock()

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            self._data = {"version": STORE_VERSION, "clock": 0, "entries": {}}
            if self.path is not None:
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except OSError, ValueError:
                    data = None
                if isinstance(data, dict) and data.get("version") == STORE_VERSION:
                    self._data = data
        return self._data

    def _save(self) -> None:
        if self.path is None or self._data is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        temporary.write_text(json.dumps(self._data, separators=(",", ":")), encoding="utf-8")
        temporary.replace(self.path)

    def view(self, key: StoreKey) -> Learned:
        with self._lock:
            return self._view(key)

    def record(self, key: StoreKey, kind: str, **measured: Any) -> None:
        """One run's measurement; see `_record` for what it takes."""
        with self._lock:
            self._record(key, kind, **measured)

    def forget(self, node: str) -> int:
        with self._lock:
            return self._forget(node)

    def _view(self, key: StoreKey) -> Learned:
        """What a fit reads: a correction per kind of device, and needs and seconds per settings."""
        prefix = key.text("")
        corrections: dict[str, float] = {}
        peaks: dict[tuple[str, str], float] = {}
        seconds: dict[tuple[str, str], float] = {}
        for text, entry in self._load()["entries"].items():
            if not text.startswith(prefix):
                continue
            kind = text[len(prefix) :]
            correction = _correction(entry.get("ratios") or [])
            if correction is not None:
                corrections[kind] = correction
            for settings, mb in (entry.get("peaks") or {}).items():
                peaks[(kind, settings)] = float(mb)
            for settings, value in (entry.get("seconds") or {}).items():
                seconds[(kind, settings)] = float(value)
        return Learned(corrections, peaks, seconds)

    def _record(
        self,
        key: StoreKey,
        kind: str,
        *,
        settings: str,
        ok: bool,
        need_mb: float | None,
        working_mb: float | None,
        weights_mb: float | None,
        outside_mb: float = 0.0,
        seconds: float | None = None,
    ) -> None:
        """One run's measurement. `need_mb` is what it used on the device, in the fit's terms (on a
        card, torch's peak reserved memory plus the memory outside torch; on the processor, the
        growth of its resident memory); `working_mb` is the working estimate before any
        correction, and `weights_mb` the weights it held on the device. A failed run's need is a
        lower bound: it adds a ratio only above 1, and no need or seconds."""
        data = self._load()
        entries: dict[str, Any] = data["entries"]
        text = key.text(kind)
        entry = entries.pop(text, None) or {"ratios": [], "peaks": {}, "seconds": {}}
        if (
            need_mb is not None
            and working_mb is not None
            and weights_mb is not None
            and working_mb >= MIN_WORKING_MB
        ):
            ratio = max(need_mb - weights_mb - outside_mb, 0.0) / working_mb
            if ok or ratio > 1:
                entry["ratios"] = [*entry["ratios"], {"ratio": round(ratio, 4), "ok": ok}][-RECENT_RATIOS:]
        if ok and need_mb is not None:
            entry["peaks"] = _recent(entry["peaks"], settings, round(need_mb, 1))
        if ok and seconds is not None:
            entry["seconds"] = _recent(entry["seconds"], settings, round(seconds, 3))
        data["clock"] = int(data.get("clock", 0)) + 1
        entry["used"] = data["clock"]
        entries[text] = entry
        while len(entries) > STORE_ENTRIES:
            oldest = min(entries, key=lambda k: entries[k].get("used", 0))
            del entries[oldest]
        self._save()

    def _forget(self, node: str) -> int:
        """Drops everything learned about a node, every version and kind of device; how many
        entries went."""
        entries: dict[str, Any] = self._load()["entries"]
        gone = [text for text in entries if text.split("|", 1)[0] == node]
        for text in gone:
            del entries[text]
        if gone:
            self._save()
        return len(gone)


def _recent(rows: dict[str, Any], settings: str, value: float) -> dict[str, Any]:
    """The newest value for these settings, keeping the last few settings."""
    rows = {k: v for k, v in rows.items() if k != settings}
    rows[settings] = value
    return dict(list(rows.items())[-RECENT_SETTINGS:])


def _correction(ratios: list[dict[str, Any]]) -> float | None:
    """The largest recent ratio. It goes below 1 only when at least two successful runs agree;
    until then a smaller ratio leaves the estimate as it is."""
    if not ratios:
        return None
    largest = max(float(r["ratio"]) for r in ratios)
    if largest < 1 and sum(1 for r in ratios if r.get("ok")) < LOWER_AFTER_RUNS:
        return None
    return largest
