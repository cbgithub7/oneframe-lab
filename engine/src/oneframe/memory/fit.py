"""The fit: where a node runs and at which values, before its model loads.

Pure: the same arguments give the same fit, and no card's name is read."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from oneframe.memory.estimate import Device, Inputs, Learned, Need, Target, estimate, settings_hash
from oneframe.memory.model import PRECISION_PARAM, Change, MemoryModel, Precision
from oneframe.memory.settings import Settings, margin

# -- the machine, as a fit sees it ---------------------------------------------------------------


@dataclass(frozen=True)
class Checkpoints:
    """Which of a node's checkpoints are downloaded (spec 003). The fit never changes to one that is
    not, says so among what it skipped, and when only such a checkpoint would fit, names it and what
    it would take to download. It never changes to one just because it is on disk."""

    by: str  # the choice param that picks a checkpoint's files
    present: frozenset[str]
    to_fetch: Mapping[str, int] = field(default_factory=dict)  # bytes still to download, by checkpoint


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
        checkpoints: Checkpoints | None = None,
    ) -> None:
        self.model = model
        self.checkpoints = checkpoints
        self.not_downloaded: set[str] = set()
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
            self.checkpoints,
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
        if self.checkpoints is not None and self.checkpoints.by in change.set:
            wanted = str(change.set[self.checkpoints.by])
            if wanted not in self.checkpoints.present:
                self.not_downloaded.add(wanted)
                return f"{self.checkpoints.by} {wanted} is not downloaded"
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
        only = self.only_if_downloaded()
        if only is not None:
            return self.failed(only)
        if self.explicit_blocked:
            for device in [d for d in (first, *usable) if d is not None]:
                allowed = self.points(device, not self.settings.never_reduce_quality)
                if allowed:
                    point = self.measure(allowed[-1])
                    why = "Nothing fits in the free memory, and the graph sets what the engine would change"
                    return self.result(point, "tried_anyway", {"kind": "tried_anyway", "why": why})
        return self.failed(self.memory_message(usable))

    def only_if_downloaded(self) -> str | None:
        """When nothing on disk fits but a checkpoint that is not downloaded would: which, and its
        size to download."""
        if self.checkpoints is None or not self.not_downloaded:
            return None
        anyway = self.fresh()
        anyway.checkpoints = None
        found = anyway.run(None)
        if found.outcome != "fits":
            return None
        wanted = str(found.values.get(self.checkpoints.by))
        if wanted in self.checkpoints.present:
            return None
        size = self.checkpoints.to_fetch.get(wanted)
        mb = f"{round(size / 1e6)} MB to download" if size is not None else "its size is not known"
        return (
            f"Only {self.checkpoints.by} {wanted} would fit in the free memory, and it is not downloaded "
            f"({mb}). Download it, or free some memory."
        )

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
    checkpoints: Checkpoints | None = None,
) -> Fit:
    """Where a node runs and at which values.

    Pure: the same arguments give the same fit, and no card's name is read. `values` are the step's
    params (with `precision`), `explicit` the ones the graph sets, `inputs` each input's `meta`,
    `devices` the manifest's list, and `after` the fit of an attempt that ran out of memory, which
    the result must be strictly smaller than. `checkpoints`: which checkpoints are downloaded, for
    a node that reads files by checkpoint (spec 003); a change to another is skipped."""
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
    fitter = _Fitter(
        model, values, frozenset(explicit), inputs, learned, settings, devices, here, system, checkpoints
    )
    return fitter.run(after)
