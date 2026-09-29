"""Run a plan: each step in order, from the cache when it can be, checked when it cannot.

Events, in order, for the app (all carry `run`):

    run.start    order, notes
    node.start   step, node, where
    node.cached  step, outputs
    stage / progress / ceiling       (forwarded from the node, with step added)
    node.done    step, seconds, peak_vram_mb, outputs
    node.failed  step, kind, message
    run.done | run.failed | run.stopped

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

from oneframe import ports
from oneframe.cache import Cache
from oneframe.child import Value
from oneframe.executors import EngineExecutor, NodeError, ProcessExecutor, Stopped
from oneframe.graph import Graph, Plan, Step, plan
from oneframe.manifest import Manifest
from oneframe.registry import Registry

LOGGER = logging.getLogger(__name__)
Emit = Callable[[dict[str, Any]], None]
TRUST_ORDER = {"measured": 0, "predicted": 1, "synthetic": 2}


class RuntimeMissing(RuntimeError):
    """A node's runtime is not installed on this machine."""


def no_runtimes(runtime: str) -> Path:
    raise RuntimeMissing(f"The runtime {runtime!r} is not installed.")


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
    ):
        self.registry = registry
        self.cache = cache
        self.runtime_python = runtime_python
        self.models_dir = models_dir
        self.log_dir = log_dir
        self.engine = EngineExecutor()

    def _executor(self, manifest: Manifest) -> EngineExecutor | ProcessExecutor:
        if manifest.run.where == "engine":
            return self.engine
        python = self.runtime_python(str(manifest.run.runtime))
        return ProcessExecutor(python, log_dir=self.log_dir)

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
            try:
                outputs, record = self._step(run_id, step, result.outputs, say, should_stop)
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

        executor = self._executor(manifest)
        say(
            {
                "event": "node.start",
                "step": step.id,
                "node": manifest.id,
                "where": manifest.run.where,
                "key": key,
            }
        )
        work = self.cache.begin(key)
        job = {
            "run": run_id,
            "step": step.id,
            "node": manifest.id,
            "entry": {"file": str(manifest.code_path), "function": manifest.run.function},
            "inputs": {p: v.to_json() for p, v in inputs.items()},
            "params": step.params,
            "out_dir": str(work),
            "device": manifest.devices[0],
            "models": str(self.models_dir) if self.models_dir else None,
        }
        started = time.monotonic()
        try:
            finished = executor.execute(job, lambda e: say(dict(e, step=step.id)), should_stop)
            outputs = self._check_outputs(step, inputs, finished.get("outputs") or {}, key)
            record = {
                "cached": False,
                "key": key,
                "node": manifest.id,
                "version": manifest.version,
                "seconds": finished.get("seconds", round(time.monotonic() - started, 3)),
                "peak_vram_mb": finished.get("peak_vram_mb"),
                "device": job["device"],
                "stats": finished.get("stats") or {},
            }
            outputs = self.cache.commit(key, work, outputs, record)
        except BaseException:
            self.cache.abandon(work)
            raise
        say(
            {
                "event": "node.done",
                "step": step.id,
                "node": manifest.id,
                "key": key,
                "seconds": record["seconds"],
                "peak_vram_mb": record["peak_vram_mb"],
                "outputs": {p: v.to_json() for p, v in outputs.items()},
            }
        )
        return outputs, record

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
