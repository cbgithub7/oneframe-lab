"""The engine as the app sees it: one long-lived process speaking NDJSON on stdin and stdout.

    python -m oneframe.server --data <data root>

Requests are `{"id": 1, "method": "nodes.list", "params": {}}` and are answered by
`{"id": 1, "result": ...}` or `{"id": 1, "error": {"message": ..., "problems": [...]}}`. Anything
that takes time (a graph run) answers at once with a run id, and its progress arrives as events,
`{"event": "node.done", ...}`, with no id. There is no polling and no local network port: the only
way in is this process's own stdin, which only the app holds.

Only one graph runs at a time: they share one GPU, and a second run would only fight the first for
memory. A run asked for while another is going is refused with the running one's id.

Runtimes (the environments heavy nodes run in) are listed, planned, installed and removed here too.
An install answers at once and reports itself as runtime.* events; one runs at a time, and a
runtime the running graph uses is neither installed over nor removed.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__, hardware, ports
from oneframe.cache import Cache
from oneframe.errors import Failure, failure
from oneframe.graph import Graph, Step, plan, step_params
from oneframe.layout import Layout, RootRefused, claim_root, default_root, keep_bytecode_under
from oneframe.memory import LearnedStore, read_settings
from oneframe.registry import Registry, discover
from oneframe.runtimes import InstallRefused, Runtimes, find_uv
from oneframe.scheduler import Scheduler
from oneframe.scratch import Scratch

LOGGER = logging.getLogger("oneframe.server")


class Engine:
    def __init__(
        self,
        data: Path,
        node_roots: list[Path],
        say: Callable[[dict[str, Any]], None],
        runtime_roots: list[Path] | None = None,
        uv: str | None = None,
        uv_home: Path | None = None,
        profile: Callable[[], dict[str, Any]] | None = None,
        packaged: bool = False,
        scratch: Scratch | None = None,
    ):
        self.data = data
        self.layout = Layout(data)
        # Before anything is written under the root: a newer layout stops the engine here.
        self.warnings = claim_root(data, packaged, __version__)
        # This process's own folder under cache/tmp/; the start sweeps only dead processes' folders.
        self.scratch = scratch or Scratch.claim(self.layout)
        self.node_roots = node_roots
        self.say = say
        self.registry: Registry = discover(node_roots)
        self.cache = Cache(self.layout.cache, work=self.scratch.folder / "runs")
        self.runtimes = Runtimes(data, runtime_roots, uv=find_uv(uv), profile=profile, uv_home=uv_home)
        self.learned = LearnedStore(data)
        # A fit reads the machine fresh, since free memory changes; a test passes its own profile.
        self._profile = profile
        self.scheduler = Scheduler(
            self.registry,
            self.cache,
            runtime_python=self.runtimes.python_for,
            runtime_env=self.runtimes.env_for,
            models_dir=self.layout.models,
            log_dir=self.layout.logs,
            machine=self._machine,
            target=self.runtimes.device_target,
            settings=lambda: read_settings(data),
            learned=self.learned,
            tmp_root=self.scratch.folder,
            runtime_caches=self.runtimes.caches_for,
        )
        self._install: threading.Thread | None = None
        # the running graph: its id, its stop flag, and the runtimes its nodes run in
        self._run: tuple[str, threading.Event, set[str]] | None = None
        self._run_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.methods: dict[str, Callable[[dict[str, Any]], Any]] = {
            "engine.hello": self.hello,
            "nodes.list": self.nodes_list,
            "nodes.reload": self.nodes_reload,
            "nodes.fit": self.nodes_fit,
            "nodes.forget": self.nodes_forget,
            "ports.list": self.ports_list,
            "graph.validate": self.graph_validate,
            "graph.run": self.graph_run,
            "run.stop": self.run_stop,
            "runtimes.list": self.runtimes_list,
            "runtimes.plan": self.runtimes_plan,
            "runtimes.install": self.runtimes_install,
            "runtimes.stop": self.runtimes_stop,
            "runtimes.remove": self.runtimes_remove,
        }

    def hello(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "engine": __version__,
            "python": sys.version.split()[0],
            "data": str(self.data),
            "warnings": self.warnings,
            "nodes": len(self.registry.nodes),
            "methods": sorted(self.methods),
        }

    def nodes_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "nodes": [m.to_json() for m in sorted(self.registry.nodes.values(), key=lambda m: m.id)],
            "problems": self.registry.problems,
        }

    def nodes_reload(self, params: dict[str, Any]) -> dict[str, Any]:
        self.registry = discover(self.node_roots)
        self.scheduler.registry = self.registry
        return self.nodes_list(params)

    def _machine(self, cards: bool) -> dict[str, Any]:
        if self._profile is not None:
            return self._profile()
        return hardware.profile(self.data, cards=cards)

    def nodes_fit(self, params: dict[str, Any]) -> dict[str, Any]:
        """The fit a node would get on this machine now: `{node, params?, inputs?}`, where `inputs`
        maps a port to its value's `meta` (an image's width and height). It starts no runtime and
        loads no model library."""
        manifest = self.registry.get(str(params.get("node")))
        given = dict(params.get("params") or {})
        values, problems = step_params(manifest, given)
        if problems:
            raise ValueError("; ".join(problems))
        sizes = dict(params.get("inputs") or {})
        meta: dict[str, dict[str, Any] | None] = {
            port: None for port, spec in manifest.inputs.items() if spec.optional and port not in sizes
        }
        meta.update({port: dict(row or {}) for port, row in sizes.items() if port in manifest.inputs})
        step = Step(id=manifest.id, manifest=manifest, params=values, inputs={}, explicit=frozenset(given))
        found, settings = self.scheduler.preview(step, meta)
        return {"node": manifest.id, **found.to_json(), "settings": settings.to_json()}

    def nodes_forget(self, params: dict[str, Any]) -> dict[str, Any]:
        """Clears what this machine learned about a node; how many entries went."""
        return {"node": params.get("node"), "dropped": self.learned.forget(str(params.get("node")))}

    def ports_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "types": {
                name: {"doc": t.doc, "carrier": t.carrier, "facets": t.facets}
                for name, t in ports.types().items()
            }
        }

    def graph_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        the_plan = plan(Graph.from_json(params["graph"]), self.registry)
        return {"order": the_plan.order, "notes": the_plan.notes}

    def graph_run(self, params: dict[str, Any]) -> dict[str, Any]:
        graph = Graph.from_json(params["graph"])
        the_plan = plan(graph, self.registry)  # refuse a bad graph now, with its problems, not as an event
        used = {str(s.manifest.run.runtime) for s in the_plan.steps if s.manifest.run.where == "runtime"}
        run_id = uuid.uuid4().hex[:12]
        stop = threading.Event()

        def work() -> None:
            try:
                self.scheduler.run(graph, self.say, stop.is_set, run_id)
            except Exception as exc:  # a bug, not a node failure: say so rather than die silently
                LOGGER.exception("run %s crashed", run_id)
                trace = traceback.format_exc()[-4000:]
                self.say({"event": "run.failed", "run": run_id, **failure("engine", str(exc), detail=trace)})
            finally:
                with self._lock:
                    self._run = None

        thread = threading.Thread(target=work, name=f"run-{run_id}", daemon=True)
        with self._lock:  # the run and its thread are recorded together, so shutdown sees both
            if self._run is not None:
                raise Failure(
                    f"Run {self._run[0]} is still going; stop it first.", "busy", "Stop it, or wait.", "graph"
                )
            self._run = (run_id, stop, used)
            self._run_thread = thread
        thread.start()
        return {"run": run_id}

    def run_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._run is None or (params.get("run") and params["run"] != self._run[0]):
                return {"stopping": False}
            self._run[1].set()
            return {"stopping": True, "run": self._run[0]}

    def runtimes_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        return self.runtimes.list()

    def runtimes_plan(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.runtimes.plans(params.get("runtime"), refresh=bool(params.get("refresh")))

    def _refuse_if_running(self, runtime_id: str, doing: str) -> None:
        with self._lock:
            run = self._run
        if run is not None and runtime_id in run[2]:
            raise InstallRefused(
                f"Run {run[0]} is using {runtime_id}; stop it before {doing} the runtime.", "in_use"
            )

    def runtimes_install(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(params["runtime"])
        self._refuse_if_running(runtime_id, "installing")
        claimed = self.runtimes.begin_install(runtime_id, params.get("build"))
        if claimed is None:
            return {"runtime": runtime_id, "already": True}
        runtime, build, stop = claimed

        def work() -> None:
            try:
                self.runtimes.run_install(runtime, build, stop, self.say)
            except Exception:  # already reported as runtime.failed; keep the engine up
                LOGGER.exception("installing %s crashed", runtime_id)

        self._install = threading.Thread(target=work, name=f"install-{runtime_id}", daemon=True)
        self._install.start()
        return {"runtime": runtime_id, "build": build}

    def runtimes_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        return {"stopping": self.runtimes.stop(str(params["runtime"]))}

    def runtimes_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(params["runtime"])
        self._refuse_if_running(runtime_id, "removing")
        return self.runtimes.remove(runtime_id)

    def shutdown(self, timeout: float = 30) -> None:
        """Stop a run and an install that are still going, and wait for them, so that no node
        process holding the card and no uv outlives the engine."""
        with self._lock:
            running = self._run
            run_thread = self._run_thread
        if running is not None:
            running[1].set()  # the executor sees it within a poll and kills the node's process
        installing = self.runtimes.installing()
        if installing is not None:
            self.runtimes.stop(installing)
        deadline = time.monotonic() + timeout  # one budget for both, not one each
        if run_thread is not None:
            run_thread.join(max(deadline - time.monotonic(), 0))
        if self._install is not None:
            self._install.join(max(deadline - time.monotonic(), 0))
        self.scratch.release()

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        req_id = message.get("id")
        method = message.get("method")
        fn = self.methods.get(str(method))
        if fn is None:
            return {
                "id": req_id,
                "error": failure("request", f"Unknown method {method!r}.", "unknown_method"),
            }
        try:
            return {"id": req_id, "result": fn(dict(message.get("params") or {}))}
        except Failure as exc:  # a refusal with a declared kind and reason, for a person
            return {"id": req_id, "error": exc.to_json()}
        except Exception as exc:
            LOGGER.exception("%s failed", method)
            trace = traceback.format_exc()[-4000:]
            return {"id": req_id, "error": failure("engine", f"{type(exc).__name__}: {exc}", detail=trace)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oneframe.server")
    parser.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
    parser.add_argument(
        "--nodes",
        type=Path,
        action="append",
        default=None,
        help="a folder of nodes; repeat for more (default: the built-in nodes)",
    )
    parser.add_argument(
        "--runtimes",
        type=Path,
        action="append",
        default=None,
        help="a folder of runtimes; repeat for more (default: the repo's runtimes/)",
    )
    parser.add_argument(
        "--uv", default=None, help="the uv to build runtimes with (default: UV, ONEFRAME_UV, PATH)"
    )
    parser.add_argument(
        "--uv-home", type=Path, default=None, help="uv's cache and Pythons (default: <data>/uv)"
    )
    parser.add_argument(
        "--packaged", action="store_true", help="started by the packaged app (it passes --data too)"
    )
    args = parser.parse_args(argv)
    data = args.data or default_root()
    data.mkdir(parents=True, exist_ok=True)

    # The protocol gets its own copy of stdout; fd 1 goes to stderr, so a node run in this process
    # that prints cannot corrupt an event.
    out = os.fdopen(os.dup(1), "w", encoding="utf-8", buffering=1)
    sys.stdout.flush()
    os.dup2(2, 1)
    sys.stdout = sys.stderr
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    write_lock = threading.Lock()

    def say(payload: dict[str, Any]) -> None:
        line = json.dumps(payload, default=str, ensure_ascii=False)
        with write_lock:
            out.write(line + "\n")
            out.flush()

    try:
        claim_root(data, args.packaged, __version__)  # before the scratch folder is written
    except RootRefused as exc:
        say({"event": "engine.failed", **exc.to_json()})
        return 2
    keep_bytecode_under(data)
    scratch = Scratch.claim(Layout(data))
    scratch.use_for_process()  # the engine's temporary files, and uv's, stay under the root
    engine = Engine(
        data,
        args.nodes or [BUILTIN_NODES_DIR],
        say,
        args.runtimes,
        args.uv,
        args.uv_home,
        packaged=args.packaged,
        scratch=scratch,
    )
    say({"event": "engine.ready", **engine.hello({})})
    stdin = open(sys.stdin.fileno(), encoding="utf-8", errors="replace", closefd=False)  # noqa: SIM115
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            say({"event": "engine.warning", "message": "A request was not JSON."})
            continue
        reply = engine.handle(message)
        if reply is not None:
            say(reply)
    engine.shutdown()  # stdin closed: the app is quitting
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
