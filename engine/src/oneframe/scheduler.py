"""Run a plan: each step in order, from the cache when it can be, checked when it cannot.

Events, in order, for the app (all carry `run`; contracts/protocol.json declares their fields, and
protocol.py checks each as it is sent, a node's own as they are passed on):

    run.start      order, notes
    node.cached    step, outputs                  (the result at the step's own values)
    node.queued    step, node, device, behind     (waiting its turn for a slot; spec 023)
    node.fit       step, attempt, and the fit     (spec 002; not for an engine node without a model)
    node.cached    step, outputs, fit             (a reduced result the fit allows)
    node.start     step, node, where, attempt
    stage / progress / ceiling                    (forwarded from the node, with step added)
    node.step_oom  step, stage, way, next, message (ctx.fallbacks moved to its next way)
    node.done      step, seconds, peaks, made_with, fit, outputs
    node.oom       step, attempt, peaks, message  (before the one retry, or the failure)
    node.failed    step, node, kind, reason, message, next, retry, detail, fits, peaks
    node.skipped   step, node, waited_on          (a step it needs failed or was skipped)
                   step, node, edge, kind, message, retry   (a value did not fit its input)
    run.done | run.failed | run.stopped

A failure carries a declared kind and reason (errors.py). A value whose facets do not fit the port
it reaches is no node's fault: there is no node.failed, and run.failed names the edge.

A failure stops only what depends on it: every step whose inputs come from a failed or skipped
step is skipped and never starts, and the run goes on with the rest. It then ends run.failed with
the first failure's kind, reason and message, its step (or edge), and `failed` and `skipped`, the
steps of each in order. A Stop still ends the whole run.

Before a node's model loads, the engine fits it to the memory this machine has free (`oneframe.memory`):
the device, the precision and the settings the person left alone, never a checkpoint that is not
downloaded. Out of memory is answered once, with a fit strictly smaller than the one that ran out.
Each attempt runs where the scheduler's `Executors` say (executors.py): by default, a new process
for each attempt of a runtime node.

Several runs go at once, each attempt holding its device's slot (slots.py), taken after the cache
and before the fit; a step another run is making at the same key waits for it, saying node.queued
with no device, and is answered from the cache.

A cached result needs nothing more. Otherwise, before node.start and in this order: the slot, the
fit, the runtime check, then the files check (spec 003): every file the node reads at its fitted
values must be present in the model store, or it fails with kind `files` and no process starts.
The node reaches them through `ctx.file(name)`. For as long as a run lasts, this process lists
every file its nodes could read, so a remove in any process leaves them alone.

Values are checked where they cross a port: a node's declared outputs against its manifest
(every output present, every facet stated, each from the allowed values), and each input against
what the consumer accepts, because an output that leaves a facet open is only known at run time
(kind `edge`).
Trust flows with the value: an output that does not state its trust inherits the weakest trust of
its inputs, so a mesh built from synthetic views never comes out labelled measured. So do its
sources: every output's `meta.sources` names the values it descends from that no step made from
another (a photo), by their keys, so two values from different photos can be told apart.
"""

from __future__ import annotations

import contextlib
import logging
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from oneframe import ports, protocol
from oneframe.cache import Cache, Held, code_digest
from oneframe.errors import ContractError, EdgeMismatch
from oneframe.executors import Executor, Executors, NodeError, OneProcessPerRun, Stopped
from oneframe.graph import Graph, Plan, Step, plan
from oneframe.journal import end_when_free, engine_crash
from oneframe.layout import Layout
from oneframe.manifest import Manifest
from oneframe.memory.estimate import Inputs, Learned, Target, evaluate, settings_hash, weights_mb
from oneframe.memory.fit import Fit, can_retry, fit
from oneframe.memory.learned import LearnedStore, StoreKey
from oneframe.memory.model import PRECISION_PARAM
from oneframe.memory.settings import Settings
from oneframe.models.runs import FilesMissing, check_files, checkpoints_for, reading
from oneframe.models.store import Store
from oneframe.node import Emit, Value
from oneframe.node.devices import CPU, CUDA
from oneframe.node.job import Done, Job
from oneframe.outputs import check as outputs_check
from oneframe.outputs import flat
from oneframe.registry import Registry
from oneframe.runtimes.manager import NoRuntimes, RuntimeMissing, RuntimeSide
from oneframe.slots import ENGINE, POLL_S, Place, Slot, Slots

LOGGER = logging.getLogger(__name__)


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

    inputs: Inputs
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
    # What every step event says beside its own fields: an item's `item` and `of` (spec 021).
    tag: dict[str, int] = field(default_factory=dict)

    def event(self, name: str, fields: dict[str, Any]) -> None:
        """Say the event `name`, with the run's id, checked (protocol.py)."""
        self.say(protocol.event(name, **{**fields, **self.tag, "run": self.id}))

    def ending(self, name: str, fields: dict[str, Any]) -> dict[str, Any]:
        """The run's ending, with its id, checked; the run sends it once its place is free."""
        return protocol.event(name, **{**fields, "run": self.id})

    def passed_on(self, event: dict[str, Any]) -> None:
        """An event a node said, with the run's id, checked here: a runtime's Python cannot."""
        self.say(protocol.check_event(dict(event, **self.tag, run=self.id)))


@dataclass
class _StepState:
    """One step across its attempts: its inputs, what its fits read (none for an engine node without
    a memory model), and for node.failed each fit and each failed attempt's peaks."""

    step: Step
    inputs: dict[str, Held] = field(default_factory=dict)
    context: _Context | None = None
    fits: list[Fit] = field(default_factory=list)
    peaks: list[dict[str, Any]] = field(default_factory=list)
    item: dict[str, int] = field(default_factory=dict)  # for a step run for each: `item`, `of`
    # The slot it holds (spec 023), its place in that slot's line, and, once a fit moved it to the
    # slot it chose, the one device its fits are held to from then on.
    slot: Slot | None = None
    place: Place | None = None
    devices: tuple[str, ...] | None = None


@dataclass(eq=False)
class _Maker:
    """A step making a result, by its key: its place, and whether it has ended."""

    place: Place
    ended: threading.Event = field(default_factory=threading.Event)

    def wait(self, stop: threading.Event) -> None:
        """Until it has ended; Stopped if `stop` is set first."""
        while not self.ended.wait(POLL_S):
            if stop.is_set():
                raise Stopped()


class _Making:
    """The steps being made across a scheduler's runs, by their key at the step's own values."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_key: dict[str, _Maker] = {}

    def claim(self, key: str, place: Place) -> _Maker | None:
        """None when `place` is now the step making `key`; else the step that is."""
        with self._lock:
            found = self._by_key.get(key)
            if found is None:
                self._by_key[key] = _Maker(place)
            return found

    def done(self, key: str, place: Place) -> None:
        """`place` has ended making `key`: a step waiting for it goes on."""
        with self._lock:
            found = self._by_key.get(key)
            if found is not None and found.place is place:
                del self._by_key[key]
                found.ended.set()


@dataclass
class RunResult:
    run: str
    status: str  # done | failed | stopped
    outputs: dict[str, dict[str, Value]] = field(default_factory=dict)  # each step's single values
    collections: dict[str, dict[str, list[Value]]] = field(default_factory=dict)  # and collections
    records: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: dict[str, Any] | None = None  # the first failure, as node.failed (or run.failed) said it
    failed: list[str] = field(default_factory=list)  # the steps that failed, in order
    skipped: list[str] = field(default_factory=list)  # the steps that did not run because of one


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
        imports: Path | None = None,
        slots: Slots | None = None,
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
        self.imports = imports  # where a file param's import is found (the layout's inputs)
        # One attempt at a time on each card, the processor and the engine's process, across every
        # run this scheduler runs (spec 023).
        self.slots = slots if slots is not None else Slots()
        self._making = _Making()  # the key of each step being made, across every run

    def _key(
        self, run: _RunState, manifest: Manifest, values: dict[str, Any], inputs: dict[str, Held]
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
        release: Callable[[], None] = lambda: None,
        upto: list[str] | None = None,
    ) -> RunResult:
        """Run a graph, its events journalled. Its ending (run.done, run.stopped or run.failed, or an
        engine bug's run.failed before the bug goes on up) is sent once the files it reads are no
        longer listed and `release` has let go of what the caller holds for it: the engine's run slot.
        `upto`: the steps the run is for, and only they and what they depend on run (spec 022)."""
        run_id = run_id or uuid.uuid4().hex[:12]
        stop = stop if stop is not None else threading.Event()

        def crashed(exc: BaseException) -> dict[str, Any]:
            return protocol.event("run.failed", **engine_crash(exc, run=run_id))

        work = lambda kept: self._run(graph, kept, stop, run_id, upto)  # noqa: E731
        return end_when_free(emit, self.journal_dir, f"run-{run_id}", work, release, crashed)

    def _run(
        self, graph: Graph, emit: Emit, stop: threading.Event, run_id: str, upto: list[str] | None = None
    ) -> tuple[RunResult, dict[str, Any]]:
        run = _RunState(run_id, emit, stop)
        the_plan: Plan = plan(graph, self.registry, self.imports, upto)
        run.event("run.start", {"order": the_plan.order, "notes": the_plan.notes})
        # Every file the graph's nodes could read, at any checkpoint, is listed for the whole run,
        # so a remove in any process leaves them alone until it ends.
        files = sorted({spec.sha256 for step in the_plan.steps for spec in step.manifest.files})
        with reading(self.tmp_root, files):
            return self._steps(the_plan, run)

    def _steps(self, the_plan: Plan, run: _RunState) -> tuple[RunResult, dict[str, Any]]:
        """Each step in order; the run's result, and its ending, which the run sends. A failure
        stops only the steps that need what it would have made."""
        result = RunResult(run=run.id, status="done")
        done: dict[str, dict[str, Held]] = {}  # every step's values, singles and collections
        ending: dict[str, Any] | None = None  # the first failure, as run.failed carries it
        for step in the_plan.steps:
            if run.stop.is_set():
                result.status = "stopped"
                return result, run.ending("run.stopped", {"step": step.id})
            lost = set(result.failed) | set(result.skipped)
            waited_on = next((src for src, _ in step.inputs.values() if src in lost), None)
            if waited_on is not None:
                result.skipped.append(step.id)
                run.event("node.skipped", {"step": step.id, "node": step.manifest.id, "waited_on": waited_on})
                continue
            state = _StepState(step)
            try:
                outputs, record = self._each(run, state, done) if step.each else self._step(run, state, done)
            except Stopped:
                result.status = "stopped"
                return result, run.ending("run.stopped", {"step": step.id})
            except EdgeMismatch as exc:  # no node's fault: reported against the edge, not the step
                error = exc.to_json(edge=exc.edge)
                result.skipped.append(step.id)
                run.event("node.skipped", {**error, "step": step.id, "node": step.manifest.id, **state.item})
                result.error = result.error or error
                ending = ending or error
                continue
            except (NodeError, RuntimeMissing, ContractError, FilesMissing) as exc:
                error = exc.to_json(
                    step=step.id, node=step.manifest.id, detail=getattr(exc, "detail", ""), **state.item
                )
                if state.fits:
                    error["fits"] = [f.to_json() for f in state.fits]
                if state.peaks:
                    error["peaks"] = state.peaks
                result.failed.append(step.id)
                run.event("node.failed", error)
                result.error = result.error or error
                ending = ending or exc.to_json(step=step.id)
                continue
            done[step.id] = outputs
            result.outputs[step.id] = {p: v for p, v in outputs.items() if not isinstance(v, list)}
            if any(isinstance(v, list) for v in outputs.values()):
                result.collections[step.id] = {p: v for p, v in outputs.items() if isinstance(v, list)}
            result.records[step.id] = record
        if ending is not None:
            result.status = "failed"
            return result, run.ending(
                "run.failed", {**ending, "failed": list(result.failed), "skipped": list(result.skipped)}
            )
        manifests = {step.id: step.manifest for step in the_plan.steps}
        outputs_json = {sid: as_json(manifests[sid], outs) for sid, outs in done.items()}
        return result, run.ending("run.done", {"outputs": outputs_json})

    def _gather(
        self, step: Step, done: dict[str, dict[str, Held]], item: int | None = None
    ) -> dict[str, Held]:
        """Each input's value, or collection, held to what the input accepts: a collection's
        items each, one that does not fit named by its index. For the run of one `item` of a step
        run for each, each input fed item by item gives that item."""
        inputs: dict[str, Held] = {}
        for port, (src, src_port) in step.inputs.items():
            held = done[src][src_port]
            indexed: list[tuple[int | None, Value]]
            if item is not None and port in step.each and isinstance(held, list):
                held = held[item]
                indexed = [(item, held)]
            else:
                indexed = list(enumerate(held)) if isinstance(held, list) else [(None, held)]
            for index, value in indexed:
                why = ports.value_mismatch(value.facets, step.manifest.inputs[port])
                if why:
                    at = f"[{index}]" if index is not None else ""
                    source, target = f"{src}.{src_port}", f"{step.id}.{port}"
                    raise EdgeMismatch(f"{source}{at} -> {target}: {why}", source, target)
            inputs[port] = held
        return inputs

    def _each(
        self, run: _RunState, state: _StepState, done: dict[str, dict[str, Held]]
    ) -> tuple[dict[str, Held], dict[str, Any]]:
        """A step run for each (spec 021): once an item, in order, each run a single step's, its
        events carrying `item` and `of`; each output a collection of the runs' values. The first
        item that fails ends the step, the items done before it kept in the cache. With no items,
        nothing runs: the step is done, `of` 0, with empty collections."""
        step = state.step
        src, src_port = step.inputs[step.each[0]]
        fed = done[src][src_port]
        count = len(fed) if isinstance(fed, list) else 1  # a collection, as the plan says
        outputs: dict[str, Held] = {port: [] for port in step.manifest.outputs}
        if count == 0:
            run.event(
                "node.done",
                {
                    "step": step.id,
                    "node": step.manifest.id,
                    "of": 0,
                    "seconds": 0.0,
                    "peak_vram_mb": None,
                    "peak_reserved_mb": None,
                    "peak_ram_mb": None,
                    "outputs": as_json(step.manifest, outputs),
                },
            )
            return outputs, {"cached": False, "items": []}
        records: list[dict[str, Any]] = []
        for index in range(count):
            if run.stop.is_set():
                raise Stopped()  # between two items: the next one never starts
            state.item = {"item": index, "of": count}
            state.context, state.fits, state.peaks, state.devices = None, [], [], None
            made, record = self._step(replace(run, tag=state.item), state, done, index)
            for port, value in made.items():
                held = outputs[port]
                # the plan refuses a step run for each that gives a collection of its own
                assert isinstance(held, list) and isinstance(value, Value)
                held.append(value)
            records.append(record)
        return outputs, {"cached": all(r.get("cached") for r in records), "items": records}

    def _step(
        self,
        run: _RunState,
        state: _StepState,
        done: dict[str, dict[str, Held]],
        item: int | None = None,
    ) -> tuple[dict[str, Held], dict[str, Any]]:
        step = state.step
        manifest = step.manifest
        state.inputs = self._gather(step, done, item)
        key = self._key(run, manifest, step.params, state.inputs)
        state.place = Place(run.id, step.id, dict(run.tag))
        # The same work asked for twice is done once (spec 023): a step another run is making is
        # waited for, then taken the normal way, from the cache or by its own attempt.
        while (ahead := self._making.claim(key, state.place)) is not None:
            cached = self.cache.get(key)
            if cached is not None:
                return self._said_cached(run, step, key, cached)
            run.event("node.queued", {"step": step.id, "node": manifest.id, "behind": ahead.place.to_json()})
            ahead.wait(run.stop)
        try:
            cached = self.cache.get(key)
            if cached is not None:
                return self._said_cached(run, step, key, cached)
            return self._made(run, state)
        finally:
            self._making.done(key, state.place)

    def _said_cached(
        self, run: _RunState, step: Step, key: str, cached: dict[str, Held]
    ) -> tuple[dict[str, Held], dict[str, Any]]:
        """The result at the step's own values, from the cache."""
        fields = {"step": step.id, "node": step.manifest.id, "key": key}
        run.event("node.cached", {**fields, "outputs": as_json(step.manifest, cached)})
        return cached, {"cached": True, "key": key}

    def _made(self, run: _RunState, state: _StepState) -> tuple[dict[str, Held], dict[str, Any]]:
        """A step the cache does not hold, made here: holding its slot, fitted, then tried."""
        step = state.step
        manifest = step.manifest
        # An engine node without a memory model runs as it always has: on its first device, with
        # no fit and no cap (spec 002, plan decision 12), holding the engine's slot.
        if manifest.memory is None and manifest.run.where == "engine":
            with self._holding(run, state, Slot(ENGINE)):
                return self._attempt(run, state, None, 1)

        state.context = self._context(step, _meta(step, state.inputs))
        # The slot first, then the fit, so the fit reads the memory the attempt will have.
        with self._holding(run, state, _slot_for(manifest, state.context)):
            return self._tries(run, state)

    def _tries(self, run: _RunState, state: _StepState) -> tuple[dict[str, Held], dict[str, Any]]:
        """A fitted step's attempts, holding its slot: the first, and one retry after running out
        of memory, with a fit strictly smaller."""
        step = state.step
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
            if not can_retry(found):
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

    @contextlib.contextmanager
    def _holding(self, run: _RunState, state: _StepState, slot: Slot) -> Iterator[None]:
        """`slot`, held by the step for the block: waited for in turn, saying `node.queued`."""
        self._take(run, state, slot)
        try:
            yield
        finally:
            if state.slot is not None and state.place is not None:
                self.slots.give(state.slot, state.place)
                state.slot = None

    def _take(self, run: _RunState, state: _StepState, slot: Slot) -> bool:
        """Take `slot` for the step; whether it had to wait for it."""

        def queued(ahead: Place) -> None:
            fields = {"step": state.step.id, "node": state.step.manifest.id, **slot.to_json()}
            run.event("node.queued", {**fields, "behind": ahead.to_json()})

        assert state.place is not None
        waited = self.slots.take(slot, state.place, run.stop, queued)
        state.slot = slot
        return waited

    def _moved(self, run: _RunState, state: _StepState, found: Fit) -> bool:
        """Where the fit chose another slot than the one held, it is let go, the chosen one taken,
        and the step's fits held to that device from then on: once a step, so a fit that chooses
        again cannot move it back and forth. Whether to fit again: only after waiting for the
        chosen slot, since with none to wait for, nothing took memory there since the fit."""
        held = state.slot
        if state.devices is not None or held is None or held.device == ENGINE or found.outcome == "memory":
            return False
        if found.device not in (CPU, CUDA):
            return False
        chosen = Slot(CUDA, found.card) if found.device == CUDA else Slot(CPU)
        if chosen == held:
            return False
        assert state.place is not None
        self.slots.give(held, state.place)
        state.slot = None
        state.devices = (found.device,)
        return self._take(run, state, chosen)

    def _context(self, step: Step, meta: Inputs) -> _Context:
        manifest = step.manifest
        runtime = manifest.run.runtime if manifest.run.where == "runtime" else None
        target = self.runtimes.device_target(str(runtime)) if runtime else Target()
        model_hash = manifest.memory.hash if manifest.memory else ""
        key = StoreKey(manifest.id, manifest.version, model_hash, target.lock or "")
        cards = runtime is not None and CUDA in manifest.devices and target.card is not None
        return _Context(meta, target, key, cards)

    def preview(self, step: Step, meta: Inputs) -> tuple[Fit, Settings]:
        """The fit a step would get on this machine now, without running it (`nodes.fit`)."""
        return self._fit_now(step, self._context(step, meta), None)

    def _fit_now(
        self, step: Step, context: _Context, after: Fit | None, devices: tuple[str, ...] | None = None
    ) -> tuple[Fit, Settings]:
        manifest = step.manifest
        settings = self.settings()
        learned = self.learned.view(context.key) if manifest.run.where == "runtime" else Learned()
        found = fit(
            manifest.memory,
            step.params,
            step.explicit,
            context.inputs,
            self.machine(context.cards),
            context.target,
            learned,
            settings,
            devices or manifest.devices,
            after,
            checkpoints_for(self.store, manifest),
        )
        return found, settings

    def _fit(self, run: _RunState, state: _StepState, attempt: int, after: Fit | None) -> Fit:
        """Fit with a fresh look at the machine, and say so; a fit that finds no room fails here."""
        step = state.step
        assert state.context is not None
        found, settings = self._fit_now(step, state.context, after, state.devices)
        if self._moved(run, state, found):  # fitted again, holding the slot it chose
            found, settings = self._fit_now(step, state.context, after, state.devices)
        state.fits.append(found)
        run.event(
            "node.fit",
            {
                "step": step.id,
                "node": step.manifest.id,
                "attempt": attempt,
                **found.to_json(),
                "settings": settings.to_json(),
            },
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
    ) -> tuple[dict[str, Held], dict[str, Any]] | None:
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
                run.event(
                    "node.cached",
                    {
                        "step": step.id,
                        "node": step.manifest.id,
                        "key": key,
                        "fit": found.to_json(),
                        "outputs": as_json(step.manifest, cached),
                    },
                )
                return cached, {"cached": True, "key": key}
        return None

    def _said_oom(self, run: _RunState, step: Step, attempt: int, exc: NodeError) -> None:
        run.event(
            "node.oom",
            {
                "step": step.id,
                "attempt": attempt,
                "peak_reserved_mb": exc.peaks.get("peak_reserved_mb"),
                "peak_ram_mb": exc.peaks.get("peak_ram_mb"),
                "message": str(exc),
            },
        )

    def _attempt(
        self, run: _RunState, state: _StepState, found: Fit | None, attempt: int
    ) -> tuple[dict[str, Held], dict[str, Any]]:
        """One attempt at a step, at the fitted values (its own, with no fit): the runtime check, the
        files check, node.start, then the job run, its result kept, and node.done."""
        step = state.step
        manifest = step.manifest
        values = dict(found.values) if found is not None else dict(step.params)
        device = (found.device if found is not None else None) or manifest.devices[0]
        key = self._key(run, manifest, values, state.inputs)
        executor = self.executors.executor(manifest, device)  # the runtime check, then the files check
        files = check_files(self.store, manifest, values)
        run.event(
            "node.start",
            {
                "step": step.id,
                "node": manifest.id,
                "where": manifest.run.where,
                "key": key,
                "attempt": attempt,
            },
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
        self._said_done(run, manifest, job, found, outputs, record)
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
                    evaluate(manifest.memory.outside_torch, manifest.memory, values, context.inputs)[0] or 0.0
                )
            # No budget in the cap where the fit knows it is too small, or where there is none left
            # (the card already more than full): the child then caps at the free memory alone.
            precision, budget = values.get(PRECISION_PARAM), found.budget
            uncapped = found.outcome in ("tried_anyway", "off") or budget is None or budget <= 0
            cap = budget if device == CUDA and not uncapped else None
        return Job(
            node=manifest.id,
            folder=manifest.folder,
            entry=f"{manifest.run.file}:{manifest.run.function}",
            out_dir=work,
            load=manifest.run.load,
            run=run.id,
            step=step.id,
            inputs={port: held for port, held in state.inputs.items() if not isinstance(held, list)},
            items={port: held for port, held in state.inputs.items() if isinstance(held, list)},
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
            run.passed_on(dict(event, step=step_id))

        try:
            return executor.execute(job, forward, run.stop)
        except NodeError as exc:
            state.peaks.append({"attempt": job.attempt, **exc.peaks})
            if exc.kind == "oom":
                self._learn(state, found, exc.peaks, ok=False, seconds=None, outside=job.outside_torch_mb)
            raise

    def _keep(
        self, state: _StepState, job: Job, finished: Done, key: str, found: Fit | None
    ) -> tuple[dict[str, Held], dict[str, Any]]:
        """Check what the node made, say how it was made, and commit it to the cache with its record."""
        step = state.step
        manifest = step.manifest
        outputs = outputs_check(step.manifest, state.inputs, finished.outputs, key)
        made_with = {
            "device": job.device,
            "precision": job.params.get(PRECISION_PARAM),
            "changed": {k: v for k, v in job.params.items() if step.params.get(k) != v},
            "ways": finished.ways,
            "reduced": bool(found is not None and found.reduced),
        }
        reduced_input = any(_reduced(v) for v in flat(state.inputs.values()))
        inherited = _sources(state.inputs)
        for value in flat(outputs.values()):
            value.meta["made_with"] = made_with  # a node cannot claim a fit it did not get
            # nor its sources: a step fed nothing starts one, every other carries its inputs'
            value.meta["sources"] = inherited if state.inputs else [{"id": value.key, "type": value.type}]
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
        manifest: Manifest,
        job: Job,
        found: Fit | None,
        outputs: dict[str, Held],
        record: dict[str, Any],
    ) -> None:
        run.event(
            "node.done",
            {
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
                "outputs": as_json(manifest, outputs),
            },
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
        if found.device == CUDA:
            reserved = peaks.get("peak_reserved_mb")
            need = None if reserved is None else float(reserved) + outside
            weights = (
                evaluate(model.weights_on_device, model, found.values, context.inputs)[0] if model else None
            )
        else:
            need = peaks.get("peak_ram_mb")
            weights = weights_mb(model, found.values) if model else None
        try:
            self.learned.record(
                context.key,
                found.kind,
                settings=settings_hash(found.values, context.inputs),
                ok=ok,
                need_mb=need,
                working_mb=found.need.working if found.need is not None else None,
                weights_mb=weights,
                outside_mb=outside if found.device == CUDA else 0.0,  # the processor has no outside
                seconds=seconds,
            )
        except OSError:
            # Learning is a help, never a reason for a run to fail.
            LOGGER.warning("could not record what %s taught this machine", manifest.id, exc_info=True)


def _slot_for(manifest: Manifest, context: _Context) -> Slot:
    """The slot a step takes before its fit: the engine's for a node that runs there, else its
    card's when it can use a card on this machine, else the processor's."""
    if manifest.run.where == "engine":
        return Slot(ENGINE)
    if context.cards and context.target.card is not None:
        return Slot(CUDA, context.target.card)
    return Slot(CPU)


def as_json(manifest: Manifest, outputs: dict[str, Held]) -> dict[str, Any]:
    """A step's values as events carry them: a value, or a collection with its type and items."""
    out: dict[str, Any] = {}
    for port, held in outputs.items():
        if isinstance(held, list):
            out[port] = {"type": manifest.outputs[port].type, "items": [v.to_json() for v in held]}
        else:
            out[port] = held.to_json()
    return out


def _meta(step: Step, inputs: dict[str, Held]) -> Inputs:
    """Each input's meta for the fit; None for an optional input that is not connected. A
    collection taken whole says only how many items it has: `<input>.count` (spec 021)."""
    meta: dict[str, dict[str, Any] | None] = {port: None for port in step.manifest.inputs}
    for port, held in inputs.items():
        meta[port] = {"count": len(held)} if isinstance(held, list) else held.meta
    return meta


def _sources(inputs: dict[str, Held]) -> list[dict[str, str]]:
    """The values these inputs descend from that no step made from another (a photo, clicks), each
    once, by its key: the same in every graph that shares its entry (spec 019)."""
    found: dict[str, dict[str, str]] = {}
    for value in flat(inputs.values()):
        for row in value.meta.get("sources") or ():
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                found[row["id"]] = {"id": row["id"], "type": str(row.get("type", ""))}
    return [found[ident] for ident in sorted(found)]


def _reduced(value: Value) -> bool:
    """A value made at reduced quality, or from one."""
    made_with = value.meta.get("made_with")
    return bool(value.meta.get("reduced_input") or (isinstance(made_with, dict) and made_with.get("reduced")))


def _described(found: Fit, peaks: dict[str, Any]) -> str:
    """Where an attempt ran and what it used, for the message of a second oom."""
    changes = ", ".join(why.split(";")[0] for _i, why in sorted(found.reasons.items()))
    at = f" with {changes}" if changes else " at its values"
    peak = peaks.get("peak_reserved_mb") if found.device == CUDA else peaks.get("peak_ram_mb")
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
        imports=layout.inputs if layout else None,
        machine=machine,
        settings=settings,
        learned=learned,
        tmp_root=tmp_root,
    )
