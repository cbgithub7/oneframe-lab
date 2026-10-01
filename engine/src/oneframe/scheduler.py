"""Run a plan: each step in order, from the cache when it can be, checked when it cannot.

Events, in order, for the app (all carry `run`):

    run.start      order, notes
    node.cached    step, outputs                  (the result at the step's own values)
    node.fit       step, attempt, and the fit     (spec 002; not for an engine node without a model)
    node.cached    step, outputs, fit             (a reduced result the fit allows)
    node.start     step, node, where, attempt
    stage / progress / ceiling                    (forwarded from the node, with step added)
    node.step_oom  step, stage, way, next         (ctx.fallbacks moved to its next way)
    node.done      step, seconds, peaks, made_with, fit, outputs
    node.oom       step, attempt, peaks, message  (before the one retry, or the failure)
    node.failed    step, kind, message, fits
    run.done | run.failed | run.stopped

Before a node's model loads, the engine fits it to the memory this machine has free (memory.py):
the device, the precision and the settings the person left alone. Out of memory is answered once,
with a fit strictly smaller than the one that ran out, in a new process for a runtime node.

Values are checked where they cross a port: a node's declared outputs against its manifest
(every output present, every facet stated, each from the allowed values), and each input against
what the consumer accepts, because an output that leaves a facet open is only known at run time.
Trust flows with the value: an output that does not state its trust inherits the weakest trust of
its inputs, so a mesh built from synthetic views never comes out labelled measured.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe import memory, ports
from oneframe.cache import Cache
from oneframe.child import Value
from oneframe.executors import EngineExecutor, NodeError, ProcessExecutor, Stopped
from oneframe.graph import Graph, Plan, Step, plan
from oneframe.manifest import Manifest
from oneframe.memory import Fit, LearnedStore, Settings, StoreKey, Target
from oneframe.registry import Registry
from oneframe.runtimes import RuntimeMissing

LOGGER = logging.getLogger(__name__)
Emit = Callable[[dict[str, Any]], None]
TRUST_ORDER = {"measured": 0, "predicted": 1, "synthetic": 2}


def no_runtimes(runtime: str) -> Path:
    raise RuntimeMissing(f"The runtime {runtime!r} is not installed.")


def no_env(_runtime: str) -> dict[str, str]:
    return {}


def no_machine(_cards: bool) -> dict[str, Any]:
    """A machine with no card and unknown memory, so a test never depends on the machine it runs
    on. The server passes the real reader."""
    return {
        "os": "unknown",
        "gpus": [],
        "driver": None,
        "nvidia": {"found": False, "why": "not read"},
        "system": {"total_mb": None, "free_mb": None, "why": "not read"},
        "disk_free_mb": None,
        "raw": "",
    }


def no_target(_runtime: str) -> Target:
    return Target()


@dataclass
class _Trail:
    """What a step's attempts left, for node.failed: each fit and each failed attempt's peaks."""

    fits: list[Fit] = field(default_factory=list)
    peaks: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Context:
    """What a step's fits read, gathered once per step."""

    inputs: memory.Inputs
    target: Target
    key: StoreKey
    cards: bool


@dataclass
class RunResult:
    run: str
    status: str  # done | failed | stopped
    outputs: dict[str, dict[str, Value]] = field(default_factory=dict)
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: dict[str, Any] | None = None


def _weakest(trusts: list[str | None]) -> str | None:
    known = [t for t in trusts if t in TRUST_ORDER]
    return max(known, key=lambda t: TRUST_ORDER[t]) if known else None


class Scheduler:
    def __init__(
        self,
        registry: Registry,
        cache: Cache,
        runtime_python: Callable[[str], Path] = no_runtimes,
        models_dir: Path | None = None,
        log_dir: Path | None = None,
        runtime_env: Callable[[str], dict[str, str]] = no_env,
        machine: Callable[[bool], dict[str, Any]] = no_machine,
        target: Callable[[str], Target] = no_target,
        settings: Callable[[], Settings] = Settings,
        learned: LearnedStore | None = None,
        on_pid: Callable[[int], None] | None = None,
    ):
        self.registry = registry
        self.cache = cache
        self.runtime_python = runtime_python
        self.runtime_env = runtime_env
        self.models_dir = models_dir
        self.log_dir = log_dir
        # A fresh machine profile for each fit; True when the node can use a card, so nvidia-smi
        # is started only for those.
        self.machine = machine
        self.target = target
        self.settings = settings
        self.learned = learned or LearnedStore(None)
        self.on_pid = on_pid
        self.engine = EngineExecutor()

    def _executor(self, manifest: Manifest) -> EngineExecutor | ProcessExecutor:
        if manifest.run.where == "engine":
            return self.engine
        runtime = str(manifest.run.runtime)
        python = self.runtime_python(runtime)
        return ProcessExecutor(
            python, env=self.runtime_env(runtime), log_dir=self.log_dir, on_pid=self.on_pid
        )

    def run(
        self,
        graph: Graph,
        emit: Emit,
        should_stop: Callable[[], bool] = lambda: False,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:12]
        result = RunResult(run=run_id, status="done")

        def say(event: dict[str, Any]) -> None:
            emit(dict(event, run=run_id))

        the_plan: Plan = plan(graph, self.registry)
        say({"event": "run.start", "order": the_plan.order, "notes": the_plan.notes})
        for step in the_plan.steps:
            if should_stop():
                result.status = "stopped"
                say({"event": "run.stopped", "step": step.id})
                return result
            trail = _Trail()
            try:
                outputs, record = self._step(run_id, step, result.outputs, say, should_stop, trail)
            except Stopped:
                result.status = "stopped"
                say({"event": "run.stopped", "step": step.id})
                return result
            except (NodeError, RuntimeMissing, ValueError) as exc:
                kind = (
                    exc.kind
                    if isinstance(exc, NodeError)
                    else ("runtime" if isinstance(exc, RuntimeMissing) else "contract")
                )
                error = {
                    "step": step.id,
                    "node": step.manifest.id,
                    "kind": kind,
                    "message": str(exc),
                    "detail": getattr(exc, "detail", ""),
                }
                if isinstance(exc, RuntimeMissing):
                    error["reason"] = exc.reason  # unknown, blocked, installing, not installed, out of date
                if trail.fits:
                    error["fits"] = [f.to_json() for f in trail.fits]
                if trail.peaks:
                    error["peaks"] = trail.peaks
                result.status = "failed"
                result.error = error
                say(dict(error, event="node.failed"))
                say({"event": "run.failed", "step": step.id, "kind": kind, "message": str(exc)})
                return result
            result.outputs[step.id] = outputs
            result.records[step.id] = record
        say(
            {
                "event": "run.done",
                "outputs": {
                    sid: {p: v.to_json() for p, v in outs.items()} for sid, outs in result.outputs.items()
                },
            }
        )
        return result

    def _gather(self, step: Step, done: dict[str, dict[str, Value]]) -> dict[str, Value]:
        inputs: dict[str, Value] = {}
        for port, (src, src_port) in step.inputs.items():
            value = done[src][src_port]
            why = ports.value_mismatch(value.facets, step.manifest.inputs[port])
            if why:
                raise ValueError(f"{step.id}.{port}: {why} (from {src}.{src_port})")
            inputs[port] = value
        return inputs

    def _step(
        self,
        run_id: str,
        step: Step,
        done: dict[str, dict[str, Value]],
        say: Emit,
        should_stop: Callable[[], bool],
        trail: _Trail,
    ) -> tuple[dict[str, Value], dict[str, Any]]:
        manifest = step.manifest
        inputs = self._gather(step, done)
        key = self.cache.key(manifest, step.params, inputs)
        cached = self.cache.get(key)
        if cached is not None:
            say(
                {
                    "event": "node.cached",
                    "step": step.id,
                    "node": manifest.id,
                    "key": key,
                    "outputs": {p: v.to_json() for p, v in cached.items()},
                }
            )
            return cached, {"cached": True, "key": key}

        # An engine node without a memory model runs as it always has: on its first device, with
        # no fit and no cap (spec 002, plan decision 12).
        if manifest.memory is None and manifest.run.where == "engine":
            return self._attempt(run_id, step, inputs, say, should_stop, None, None, 1, trail)

        context = self._context(step, _meta(step, inputs))
        found = self._fit(step, context, say, 1, None, trail)
        served = self._cached_down(step, found, inputs, say)
        if served is not None:
            return served
        try:
            return self._attempt(run_id, step, inputs, say, should_stop, found, context, 1, trail)
        except NodeError as first:
            if first.kind != "oom":
                raise
            self._said_oom(step, 1, first, say)
            if not memory.can_retry(found):
                raise
            again = self._fit(step, context, say, 2, found, trail)
            served = self._cached_down(step, again, inputs, say)
            if served is not None:
                return served
            try:
                return self._attempt(run_id, step, inputs, say, should_stop, again, context, 2, trail)
            except NodeError as second:
                if second.kind != "oom":
                    raise
                self._said_oom(step, 2, second, say)
                message = (
                    f"Ran out of memory twice: first {_described(found, first.peaks)}, "
                    f"then {_described(again, second.peaks)}. {second}"
                )
                raise NodeError("oom", message, second.detail, second.peaks) from second

    def _context(self, step: Step, meta: memory.Inputs) -> _Context:
        manifest = step.manifest
        runtime = manifest.run.runtime if manifest.run.where == "runtime" else None
        target = self.target(str(runtime)) if runtime else Target()
        model_hash = manifest.memory.hash if manifest.memory else ""
        key = StoreKey(manifest.id, manifest.version, model_hash, target.lock or "")
        cards = runtime is not None and "cuda" in manifest.devices and target.card is not None
        return _Context(meta, target, key, cards)

    def preview(self, step: Step, meta: memory.Inputs) -> tuple[Fit, Settings]:
        """The fit a step would get on this machine now, without running it (`nodes.fit`)."""
        return self._fit_now(step, self._context(step, meta), None)

    def _fit_now(self, step: Step, context: _Context, after: Fit | None) -> tuple[Fit, Settings]:
        manifest = step.manifest
        settings = self.settings()
        learned = self.learned.view(context.key) if manifest.run.where == "runtime" else memory.Learned()
        found = memory.fit(
            manifest.memory,
            step.params,
            step.explicit,
            context.inputs,
            self.machine(context.cards),
            context.target,
            learned,
            settings,
            manifest.devices,
            after,
        )
        return found, settings

    def _fit(
        self, step: Step, context: _Context, say: Emit, attempt: int, after: Fit | None, trail: _Trail
    ) -> Fit:
        """Fit with a fresh look at the machine, and say so; a fit that finds no room fails here."""
        manifest = step.manifest
        found, settings = self._fit_now(step, context, after)
        trail.fits.append(found)
        say(
            {
                "event": "node.fit",
                "step": step.id,
                "node": manifest.id,
                "attempt": attempt,
                **found.to_json(),
                "settings": settings.to_json(),
            }
        )
        if found.outcome == "memory":
            if after is not None:  # the retry found nothing smaller: the attempt's oom stands
                raise NodeError(
                    "oom", f"Ran out of memory, and {found.message[0].lower()}{found.message[1:]}"
                )
            raise NodeError("memory", found.message)
        return found

    def _cached_down(
        self, step: Step, found: Fit, inputs: dict[str, Value], say: Emit
    ) -> tuple[dict[str, Value], dict[str, Any]] | None:
        """The best cached result the fit allows: after each change that costs quality, in order,
        up to the fitted values. Changes to settings the graph sets are never among them."""
        model = step.manifest.memory
        if model is None or not found.reduced:
            return None
        values = dict(step.params)
        for i in found.applied:
            change = model.changes[i]
            values.update(change.set)
            if change.costs != "quality":
                continue
            key = self.cache.key(step.manifest, values, inputs)
            cached = self.cache.get(key)
            if cached is not None:
                say(
                    {
                        "event": "node.cached",
                        "step": step.id,
                        "node": step.manifest.id,
                        "key": key,
                        "fit": found.to_json(),
                        "outputs": {p: v.to_json() for p, v in cached.items()},
                    }
                )
                return cached, {"cached": True, "key": key}
        return None

    def _said_oom(self, step: Step, attempt: int, exc: NodeError, say: Emit) -> None:
        say(
            {
                "event": "node.oom",
                "step": step.id,
                "attempt": attempt,
                "peak_reserved_mb": exc.peaks.get("peak_reserved_mb"),
                "peak_ram_mb": exc.peaks.get("peak_ram_mb"),
                "message": str(exc),
            }
        )

    def _attempt(
        self,
        run_id: str,
        step: Step,
        inputs: dict[str, Value],
        say: Emit,
        should_stop: Callable[[], bool],
        found: Fit | None,
        context: _Context | None,
        attempt: int,
        trail: _Trail,
    ) -> tuple[dict[str, Value], dict[str, Any]]:
        manifest = step.manifest
        values = dict(found.values) if found is not None else dict(step.params)
        device = (found.device if found is not None else None) or manifest.devices[0]
        key = self.cache.key(manifest, values, inputs)
        executor = self._executor(manifest)
        say(
            {
                "event": "node.start",
                "step": step.id,
                "node": manifest.id,
                "where": manifest.run.where,
                "key": key,
                "attempt": attempt,
            }
        )
        work = self.cache.begin(key)
        job: dict[str, Any] = {
            "run": run_id,
            "step": step.id,
            "node": manifest.id,
            "entry": {"file": str(manifest.code_path), "function": manifest.run.function},
            "inputs": {p: v.to_json() for p, v in inputs.items()},
            "params": values,
            "out_dir": str(work),
            "device": device,
            "models": str(self.models_dir) if self.models_dir else None,
            "attempt": attempt,
        }
        outside = 0.0
        if found is not None and context is not None:
            if manifest.memory is not None:
                outside = (
                    memory.evaluate(manifest.memory.outside_torch, manifest.memory, values, context.inputs)[0]
                    or 0.0
                )
            # No budget in the cap where the fit knows it is too small, or where there is none left
            # (the card already more than full): the child then caps at the free memory alone.
            budget = found.budget
            uncapped = found.outcome in ("tried_anyway", "off") or budget is None or budget <= 0
            job.update(
                precision=values.get(memory.PRECISION_PARAM),
                memory_budget_mb=found.budget,
                vram_cap_mb=budget if device == "cuda" and not uncapped else None,
                outside_torch_mb=outside,
            )
            if manifest.run.where == "runtime" and device == "cpu":
                job["env"] = {"CUDA_VISIBLE_DEVICES": "-1"}  # a processor run sees no card
        started = time.monotonic()

        def forward(event: dict[str, Any]) -> None:
            if event.get("event") == "step_oom":
                event = dict(event, event="node.step_oom")
            say(dict(event, step=step.id))

        try:
            try:
                finished = executor.execute(job, forward, should_stop)
            except NodeError as exc:
                trail.peaks.append({"attempt": attempt, **exc.peaks})
                if exc.kind == "oom":
                    self._learn(step, found, context, exc.peaks, ok=False, seconds=None, outside=outside)
                raise
            outputs = self._check_outputs(step, inputs, finished.get("outputs") or {}, key)
            made_with = {
                "device": device,
                "precision": values.get(memory.PRECISION_PARAM),
                "changed": {k: v for k, v in values.items() if step.params.get(k) != v},
                "ways": finished.get("ways") or {},
                "reduced": bool(found is not None and found.reduced),
            }
            reduced_input = any(_reduced(v) for v in inputs.values())
            for value in outputs.values():
                value.meta["made_with"] = made_with  # a node cannot claim a fit it did not get
                if reduced_input:
                    value.meta["reduced_input"] = True
            record = {
                "cached": False,
                "key": key,
                "node": manifest.id,
                "version": manifest.version,
                "seconds": finished.get("seconds", round(time.monotonic() - started, 3)),
                "peak_vram_mb": finished.get("peak_vram_mb"),
                "peak_reserved_mb": finished.get("peak_reserved_mb"),
                "peak_ram_mb": finished.get("peak_ram_mb"),
                "device": device,
                "made_with": made_with,
                "stats": finished.get("stats") or {},
            }
            outputs = self.cache.commit(key, work, outputs, record)
        except BaseException:
            self.cache.abandon(work)
            raise
        self._learn(step, found, context, finished, ok=True, seconds=record["seconds"], outside=outside)
        say(
            {
                "event": "node.done",
                "step": step.id,
                "node": manifest.id,
                "key": key,
                "attempt": attempt,
                "seconds": record["seconds"],
                "peak_vram_mb": record["peak_vram_mb"],
                "peak_reserved_mb": record["peak_reserved_mb"],
                "peak_ram_mb": record["peak_ram_mb"],
                "made_with": made_with,
                **({"fit": found.to_json()} if found is not None else {}),
                "outputs": {p: v.to_json() for p, v in outputs.items()},
            }
        )
        return outputs, record

    def _learn(
        self,
        step: Step,
        found: Fit | None,
        context: _Context | None,
        peaks: dict[str, Any],
        *,
        ok: bool,
        seconds: float | None,
        outside: float,
    ) -> None:
        """What this run teaches this machine. Only runtime nodes learn: an engine node's peak is
        the whole engine's."""
        manifest = step.manifest
        if manifest.run.where != "runtime" or found is None or context is None or found.kind is None:
            return
        model = manifest.memory
        if found.device == "cuda":
            reserved = peaks.get("peak_reserved_mb")
            need = None if reserved is None else float(reserved) + outside
            weights = (
                memory.evaluate(model.weights_on_device, model, found.values, context.inputs)[0]
                if model
                else None
            )
        else:
            need = peaks.get("peak_ram_mb")
            weights = memory.weights_mb(model, found.values) if model else None
        try:
            self.learned.record(
                context.key,
                found.kind,
                settings=memory.settings_hash(found.values, context.inputs),
                ok=ok,
                need_mb=need,
                working_mb=found.need.working if found.need is not None else None,
                weights_mb=weights,
                outside_mb=outside if found.device == "cuda" else 0.0,  # the processor has no outside
                seconds=seconds,
            )
        except OSError:
            # Learning is a help, never a reason for a run to fail.
            LOGGER.warning("could not record what %s taught this machine", manifest.id, exc_info=True)

    def _check_outputs(
        self, step: Step, inputs: dict[str, Value], declared: dict[str, Any], key: str
    ) -> dict[str, Value]:
        manifest = step.manifest
        known = ports.types()
        inherited = _weakest([v.trust for v in inputs.values()])
        values: dict[str, Value] = {}
        for port, spec in manifest.outputs.items():
            row = declared.get(port)
            if row is None:
                raise NodeError("contract", f"{manifest.id} finished without its output {port!r}.")
            path = Path(row["path"])
            if not path.exists():
                raise NodeError(
                    "contract", f"{manifest.id} declared {port!r} at {path}, which does not exist."
                )
            facets = {k: v[0] for k, v in spec.facets.items() if len(v) == 1}
            for k, v in (row.get("facets") or {}).items():
                fixed = spec.facets.get(k)
                if fixed is not None and v not in fixed:
                    raise NodeError("contract", f"{manifest.id}.{port}: {k}={v} contradicts its manifest.")
                facets[k] = v
            ptype = known[spec.type]
            for k, allowed in ptype.facets.items():
                if k not in facets:
                    raise NodeError("contract", f"{manifest.id}.{port} did not say its {k}.")
                if facets[k] not in allowed:
                    raise NodeError(
                        "contract", f"{manifest.id}.{port}: {k}={facets[k]} is not a {spec.type} value."
                    )
            values[port] = Value(
                spec.type, path, facets, row.get("meta"), spec.trust or inherited, key=f"{key}:{port}"
            )
        return values


def _meta(step: Step, inputs: dict[str, Value]) -> memory.Inputs:
    """Each input's meta for the fit; None for an optional input that is not connected."""
    meta: dict[str, dict[str, Any] | None] = {port: None for port in step.manifest.inputs}
    meta.update({port: value.meta for port, value in inputs.items()})
    return meta


def _reduced(value: Value) -> bool:
    """A value made at reduced quality, or from one."""
    made_with = value.meta.get("made_with")
    return bool(value.meta.get("reduced_input") or (isinstance(made_with, dict) and made_with.get("reduced")))


def _described(found: Fit, peaks: dict[str, Any]) -> str:
    """Where an attempt ran and what it used, for the message of a second oom."""
    changes = ", ".join(why.split(";")[0] for _i, why in sorted(found.reasons.items()))
    at = f" with {changes}" if changes else " at its values"
    peak = peaks.get("peak_reserved_mb") if found.device == "cuda" else peaks.get("peak_ram_mb")
    used = f" (peak {round(peak)} MB)" if isinstance(peak, int | float) else ""
    return f"on {found.kind or found.device}{at}{used}"
