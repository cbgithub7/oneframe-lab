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
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

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


def _number(value: Any) -> bool:
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
                runs = ("cuda" in self.devices) or (cpu and "cpu" in self.devices)
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
