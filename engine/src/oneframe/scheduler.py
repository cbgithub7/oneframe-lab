"""Run a plan: each step in order, from the cache when it can be, checked when it cannot.

Events, in order, for the app (all carry `run`):

    run.start      order, notes
    node.cached    step, outputs                  (the result at the step's own values)
    node.fit       step, attempt, and the fit     (spec 002; not for an engine node without a model)
    node.cached    step, outputs, fit             (a reduced result the fit allows)
    node.start     step, node, where, attempt
    stage / progress / ceiling                    (forwarded from the node, with step added)
    node.step_oom  step, stage, way, next, message (ctx.fallbacks moved to its next way)
    node.done      step, seconds, peaks, made_with, fit, outputs
    node.oom       step, attempt, peaks, message  (before the one retry, or the failure)
    node.failed    step, node, kind, reason, message, next, retry, detail, fits, peaks
    run.done | run.failed | run.stopped

A failure carries a declared kind and reason (errors.py). A value whose facets do not fit the port
it reaches is no node's fault: there is no node.failed, and run.failed names the edge.

Before a node's model loads, the engine fits it to the memory this machine has free (memory.py):
the device, the precision and the settings the person left alone, never a checkpoint that is not
downloaded. Out of memory is answered once, with a fit strictly smaller than the one that ran out.
Each attempt runs where the scheduler's `Executors` say (executors.py): by default, a new process
for each attempt of a runtime node.

A cached result needs nothing more. Otherwise, before node.start and in this order: the fit, the
runtime check, then the files check (spec 003): every file the node reads at its fitted values must
be present in the model store, or it fails with kind `files` and no process starts. The node reaches
them through `ctx.file(name)`. For as long as a run lasts, this process lists every file its nodes
could read, so a remove in any process leaves them alone.

Values are checked where they cross a port: a node's declared outputs against its manifest
(every output present, every facet stated, each from the allowed values), and each input against
what the consumer accepts, because an output that leaves a facet open is only known at run time
(kind `edge`).
Trust flows with the value: an output that does not state its trust inherits the weakest trust of
its inputs, so a mesh built from synthetic views never comes out labelled measured.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe import memory, ports
from oneframe.cache import Cache, code_digest
from oneframe.errors import ContractError, EdgeMismatch
from oneframe.executors import Executor, Executors, NodeError, OneProcessPerRun, Stopped
from oneframe.graph import Graph, Plan, Step, plan
from oneframe.journal import engine_crash, journalled
from oneframe.layout import Layout
from oneframe.manifest import Manifest
from oneframe.memory import Fit, LearnedStore, Settings, StoreKey, Target
from oneframe.models.runs import FilesMissing, check_files, checkpoints_for, reading
from oneframe.models.store import Store
from oneframe.node import Emit, Value
from oneframe.node.job import Done, Job
from oneframe.registry import Registry
from oneframe.runtimes import NoRuntimes, RuntimeMissing, RuntimeSide

LOGGER = logging.getLogger(__name__)
TRUST_ORDER = {"measured": 0, "predicted": 1, "synthetic": 2}


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


@dataclass
class _Context:
    """What a step's fits read, gathered once per step."""

    inputs: memory.Inputs
    target: Target
    key: StoreKey
    cards: bool


@dataclass
class _RunState:
    """One run: its id, where its events go (each carrying the id), its stop, and what it reads once,
    since neither changes under a run: each runtime's part of a cache key, and each node's code."""

    id: str
    say: Emit
    stop: threading.Event
    runtime_parts: dict[str, dict[str, str] | None] = field(default_factory=dict)
    code_keys: dict[Path, str] = field(default_factory=dict)


@dataclass
class _StepState:
    """One step across its attempts: its inputs, what its fits read (none for an engine node without
    a memory model), and for node.failed each fit and each failed attempt's peaks."""

    step: Step
    inputs: dict[str, Value] = field(default_factory=dict)
    context: _Context | None = None
    fits: list[Fit] = field(default_factory=list)
    peaks: list[dict[str, Any]] = field(default_factory=list)


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
        *,
        runtimes: RuntimeSide | None = None,
        executors: Executors | None = None,
        store: Store | None = None,
        machine: Callable[[bool], dict[str, Any]] = no_machine,
        settings: Callable[[], Settings] = Settings,
        learned: LearnedStore | None = None,
        tmp_root: Path | None = None,
        journal_dir: Path | None = None,
    ):
        self.registry = registry
        self.cache = cache
        # A runtime node's target for the fit, and its build and marker hashes for its cache key.
        self.runtimes = runtimes if runtimes is not None else NoRuntimes()
        # Where each attempt runs; by default, a new process for each runtime node's attempt.
        self.executors = (
            executors if executors is not None else OneProcessPerRun(self.runtimes, tmp_root=tmp_root)
        )
        self.store = store  # the model store a node's files are read from; None: no node has any
        # A fresh machine profile for each fit; True when the node can use a card, so nvidia-smi
        # is started only for those.
        self.machine = machine
        self.settings = settings
        self.learned = learned or LearnedStore(None)
        self.tmp_root = tmp_root  # where the files a run reads are listed
        self.journal_dir = journal_dir  # where each run's events are kept (journal.py)

    def _key(
        self, run: _RunState, manifest: Manifest, values: dict[str, Any], inputs: dict[str, Value]
    ) -> str:
        runtime = None
        if manifest.run.where == "runtime":
            name = str(manifest.run.runtime)
            if name not in run.runtime_parts:
                run.runtime_parts[name] = self.runtimes.key_for(name)
            runtime = run.runtime_parts[name]
        if manifest.folder not in run.code_keys:
            run.code_keys[manifest.folder] = code_digest(manifest.folder)
        return self.cache.key(manifest, values, inputs, runtime, run.code_keys[manifest.folder])

    def run(
        self,
        graph: Graph,
        emit: Emit,
        stop: threading.Event | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or uuid.uuid4().hex[:12]

        def crashed(exc: BaseException) -> dict[str, Any]:  # what the server reports to the page
            return engine_crash(exc, event="run.failed", run=run_id)

        with journalled(emit, self.journal_dir, f"run-{run_id}", crashed) as kept:
            return self._run(graph, kept, stop if stop is not None else threading.Event(), run_id)

    def _run(self, graph: Graph, emit: Emit, stop: threading.Event, run_id: str) -> RunResult:
        def say(event: dict[str, Any]) -> None:
            emit(dict(event, run=run_id))

        run = _RunState(run_id, say, stop)
        the_plan: Plan = plan(graph, self.registry)
        say({"event": "run.start", "order": the_plan.order, "notes": the_plan.notes})
        # Every file the graph's nodes could read, at any checkpoint, is listed for the whole run,
        # so a remove in any process leaves them alone until it ends.
        files = sorted({spec.sha256 for step in the_plan.steps for spec in step.manifest.files})
        with reading(self.tmp_root, files):
            return self._steps(the_plan, run)

    def _steps(self, the_plan: Plan, run: _RunState) -> RunResult:
        result = RunResult(run=run.id, status="done")
        say = run.say
        for step in the_plan.steps:
            if run.stop.is_set():
                result.status = "stopped"
                say({"event": "run.stopped", "step": step.id})
                return result
            state = _StepState(step)
            try:
                outputs, record = self._step(run, state, result.outputs)
            except Stopped:
                result.status = "stopped"
                say({"event": "run.stopped", "step": step.id})
                return result
            except EdgeMismatch as exc:  # no node's fault: reported against the edge, not the step
                result.status = "failed"
                result.error = exc.to_json(edge=exc.edge)
                say(dict(result.error, event="run.failed"))
                return result
            except (NodeError, RuntimeMissing, ContractError, FilesMissing) as exc:
                error = exc.to_json(step=step.id, node=step.manifest.id, detail=getattr(exc, "detail", ""))
                if state.fits:
                    error["fits"] = [f.to_json() for f in state.fits]
                if state.peaks:
                    error["peaks"] = state.peaks
                result.status = "failed"
                result.error = error
                say(dict(error, event="node.failed"))
                say({"event": "run.failed", **exc.to_json(step=step.id)})
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
                source, target = f"{src}.{src_port}", f"{step.id}.{port}"
                raise EdgeMismatch(f"{source} -> {target}: {why}", source, target)
            inputs[port] = value
        return inputs

    def _step(
        self, run: _RunState, state: _StepState, done: dict[str, dict[str, Value]]
    ) -> tuple[dict[str, Value], dict[str, Any]]:
        step = state.step
        manifest = step.manifest
        state.inputs = self._gather(step, done)
        key = self._key(run, manifest, step.params, state.inputs)
        cached = self.cache.get(key)
        if cached is not None:
            run.say(
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
            return self._attempt(run, state, None, 1)

        state.context = self._context(step, _meta(step, state.inputs))
        found = self._fit(run, state, 1, None)
        served = self._cached_down(run, state, found)
        if served is not None:
            return served
        try:
            return self._attempt(run, state, found, 1)
        except NodeError as first:
            if first.kind != "oom":
                raise
            self._said_oom(run, step, 1, first)
            if not memory.can_retry(found):
                raise
            again = self._fit(run, state, 2, found)
            served = self._cached_down(run, state, again)
            if served is not None:
                return served
            try:
                return self._attempt(run, state, again, 2)
            except NodeError as second:
                if second.kind != "oom":
                    raise
                self._said_oom(run, step, 2, second)
                message = (
                    f"Ran out of memory twice: first {_described(found, first.peaks)}, "
                    f"then {_described(again, second.peaks)}. {second}"
                )
                raise NodeError("oom", message, second.detail, second.peaks) from second

    def _context(self, step: Step, meta: memory.Inputs) -> _Context:
        manifest = step.manifest
        runtime = manifest.run.runtime if manifest.run.where == "runtime" else None
        target = self.runtimes.device_target(str(runtime)) if runtime else Target()
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
            checkpoints_for(self.store, manifest),
        )
        return found, settings

    def _fit(self, run: _RunState, state: _StepState, attempt: int, after: Fit | None) -> Fit:
        """Fit with a fresh look at the machine, and say so; a fit that finds no room fails here."""
        step = state.step
        assert state.context is not None
        found, settings = self._fit_now(step, state.context, after)
        state.fits.append(found)
        run.say(
            {
                "event": "node.fit",
                "step": step.id,
                "node": step.manifest.id,
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
        self, run: _RunState, state: _StepState, found: Fit
    ) -> tuple[dict[str, Value], dict[str, Any]] | None:
        """The best cached result the fit allows: after each change that costs quality, in order,
        up to the fitted values. Changes to settings the graph sets are never among them."""
        step = state.step
        model = step.manifest.memory
        if model is None or not found.reduced:
            return None
        values = dict(step.params)
        for i in found.applied:
            change = model.changes[i]
            values.update(change.set)
            if change.costs != "quality":
                continue
            key = self._key(run, step.manifest, values, state.inputs)
            cached = self.cache.get(key)
            if cached is not None:
                run.say(
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

    def _said_oom(self, run: _RunState, step: Step, attempt: int, exc: NodeError) -> None:
        run.say(
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
        self, run: _RunState, state: _StepState, found: Fit | None, attempt: int
    ) -> tuple[dict[str, Value], dict[str, Any]]:
        """One attempt at a step, at the fitted values (its own, with no fit): the runtime check, the
        files check, node.start, then the job run, its result kept, and node.done."""
        step = state.step
        manifest = step.manifest
        values = dict(found.values) if found is not None else dict(step.params)
        device = (found.device if found is not None else None) or manifest.devices[0]
        key = self._key(run, manifest, values, state.inputs)
        executor = self.executors.executor(manifest, device)  # the runtime check, then the files check
        files = check_files(self.store, manifest, values)
        run.say(
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
        try:
            job = self._job(run, state, values, device, work, files, attempt, found)
            finished = self._execute(run, state, executor, job, found)
            outputs, record = self._keep(state, job, finished, key, found)
        except BaseException:
            self.cache.abandon(work)
            raise
        self._learn(
            state, found, finished.peaks, ok=True, seconds=finished.seconds, outside=job.outside_torch_mb
        )
        self._said_done(run, job, found, outputs, record)
        return outputs, record

    def _job(
        self,
        run: _RunState,
        state: _StepState,
        values: dict[str, Any],
        device: str,
        work: Path,
        files: dict[str, str],
        attempt: int,
        found: Fit | None,
    ) -> Job:
        """The job an attempt asks of its node: what to run, on what, and within what memory."""
        step, context = state.step, state.context
        manifest = step.manifest
        precision = budget = cap = None
        outside = 0.0
        if found is not None and context is not None:
            if manifest.memory is not None:
                outside = (
                    memory.evaluate(manifest.memory.outside_torch, manifest.memory, values, context.inputs)[0]
                    or 0.0
                )
            # No budget in the cap where the fit knows it is too small, or where there is none left
            # (the card already more than full): the child then caps at the free memory alone.
            precision, budget = values.get(memory.PRECISION_PARAM), found.budget
            uncapped = found.outcome in ("tried_anyway", "off") or budget is None or budget <= 0
            cap = budget if device == "cuda" and not uncapped else None
        return Job(
            node=manifest.id,
            folder=manifest.folder,
            entry=f"{manifest.run.file}:{manifest.run.function}",
            out_dir=work,
            load=manifest.run.load,
            run=run.id,
            step=step.id,
            inputs=dict(state.inputs),
            params=values,
            device=device,
            files=files,
            attempt=attempt,
            precision=precision,
            memory_budget_mb=budget,
            vram_cap_mb=cap,
            outside_torch_mb=outside,
        )

    def _execute(
        self, run: _RunState, state: _StepState, executor: Executor, job: Job, found: Fit | None
    ) -> Done:
        """Run the job, its events forwarded with the step. A failed attempt's peaks are kept for
        node.failed, and running out of memory teaches this machine."""
        step_id = state.step.id

        def forward(event: dict[str, Any]) -> None:
            if event.get("event") == "step_oom":
                event = dict(event, event="node.step_oom")
            run.say(dict(event, step=step_id))

        try:
            return executor.execute(job, forward, run.stop)
        except NodeError as exc:
            state.peaks.append({"attempt": job.attempt, **exc.peaks})
            if exc.kind == "oom":
                self._learn(state, found, exc.peaks, ok=False, seconds=None, outside=job.outside_torch_mb)
            raise

    def _keep(
        self, state: _StepState, job: Job, finished: Done, key: str, found: Fit | None
    ) -> tuple[dict[str, Value], dict[str, Any]]:
        """Check what the node made, say how it was made, and commit it to the cache with its record."""
        step = state.step
        manifest = step.manifest
        outputs = self._check_outputs(step, state.inputs, finished.outputs, key)
        made_with = {
            "device": job.device,
            "precision": job.params.get(memory.PRECISION_PARAM),
            "changed": {k: v for k, v in job.params.items() if step.params.get(k) != v},
            "ways": finished.ways,
            "reduced": bool(found is not None and found.reduced),
        }
        reduced_input = any(_reduced(v) for v in state.inputs.values())
        for value in outputs.values():
            value.meta["made_with"] = made_with  # a node cannot claim a fit it did not get
            if reduced_input:
                value.meta["reduced_input"] = True
        record = {
            "cached": False,
            "key": key,
            "node": manifest.id,
            "version": manifest.version,
            "seconds": finished.seconds,
            "peak_vram_mb": finished.peaks.get("peak_vram_mb"),
            "peak_reserved_mb": finished.peaks.get("peak_reserved_mb"),
            "peak_ram_mb": finished.peaks.get("peak_ram_mb"),
            "device": job.device,
            "made_with": made_with,
            "stats": finished.stats,
        }
        return self.cache.commit(key, job.out_dir, outputs, record), record

    def _said_done(
        self,
        run: _RunState,
        job: Job,
        found: Fit | None,
        outputs: dict[str, Value],
        record: dict[str, Any],
    ) -> None:
        run.say(
            {
                "event": "node.done",
                "step": job.step,
                "node": job.node,
                "key": record["key"],
                "attempt": job.attempt,
                "seconds": record["seconds"],
                "peak_vram_mb": record["peak_vram_mb"],
                "peak_reserved_mb": record["peak_reserved_mb"],
                "peak_ram_mb": record["peak_ram_mb"],
                "made_with": record["made_with"],
                **({"fit": found.to_json()} if found is not None else {}),
                "outputs": {p: v.to_json() for p, v in outputs.items()},
            }
        )

    def _learn(
        self,
        state: _StepState,
        found: Fit | None,
        peaks: dict[str, Any],
        *,
        ok: bool,
        seconds: float | None,
        outside: float,
    ) -> None:
        """What this run teaches this machine. Only runtime nodes learn: an engine node's peak is
        the whole engine's."""
        manifest, context = state.step.manifest, state.context
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


def for_root(
    data: Path | None,
    registry: Registry,
    runtimes: RuntimeSide | None,
    cache: Cache,
    *,
    machine: Callable[[bool], dict[str, Any]] = no_machine,
    settings: Callable[[], Settings] = Settings,
    learned: LearnedStore | None = None,
    tmp_root: Path | None = None,
) -> Scheduler:
    """The scheduler for a data root, as the engine and bench:fit both build it: the runtimes, a
    process per run with the root's step logs, the root's journals and model store, and `tmp_root`,
    the process's own folder under `cache/tmp/`."""
    layout = Layout(data) if data is not None else None
    side = runtimes if runtimes is not None else NoRuntimes()
    return Scheduler(
        registry,
        cache,
        runtimes=side,
        executors=OneProcessPerRun(side, tmp_root=tmp_root, log_dir=layout.step_logs if layout else None),
        store=Store(data) if data is not None else None,
        journal_dir=layout.journals if layout else None,
        machine=machine,
        settings=settings,
        learned=learned,
        tmp_root=tmp_root,
    )
