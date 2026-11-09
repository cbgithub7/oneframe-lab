"""The engine as the app sees it: one long-lived process speaking NDJSON on stdin and stdout.

    python -m oneframe.server --data <data root>

Requests are `{"id": 1, "method": "nodes.list", "params": {}}` and are answered by
`{"id": 1, "result": ...}` or `{"id": 1, "error": {"message": ..., "problems": [...]}}`. Anything
that takes time (a graph run) answers at once with a run id, and its progress arrives as events,
`{"event": "node.done", ...}`, with no id. There is no polling and no local network port: the only
way in is this process's own stdin, which only the app holds.

Requests are answered in order by one worker thread, so no two of them run at once, as the methods
were written to. Only the stops (`run.stop`, `runtimes.stop`, `models.stop`) and the reads that
answer at once (`engine.hello`, `values.locate`) are answered by the loop that reads stdin, so a
slow request never holds them up; a stop first takes each request of its kind still waiting off the
queue and answers it `{"stopped": true}`, so it is never begun. Replies carry their request's id.

Only one graph runs at a time: they share one GPU, and a second run would only fight the first for
memory. A run asked for while another is going is refused with the running one's id.

Graphs are kept as files (graphs.py): the shipped ones in the folders `--graphs` names, the
person's under the data root. `graph.fits` answers what a port could connect to, by the rule
`plan()` follows.

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
from collections import deque
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_GRAPHS_DIR, BUILTIN_NODES_DIR, __version__, graphs, hardware, inputs, ports
from oneframe.cache import Cache
from oneframe.errors import Failure, failure
from oneframe.graph import Graph, Step, fits, plan, step_params
from oneframe.journal import engine_crash
from oneframe.layout import Layout, RootRefused, claim_root, open_root
from oneframe.memory.learned import LearnedStore
from oneframe.memory.settings import read_settings
from oneframe.models.catalog import listing, remove_node, remove_unpinned
from oneframe.models.jobs import Jobs
from oneframe.models.runs import in_use
from oneframe.node import Emit
from oneframe.reading import is_sha256
from oneframe.registry import Registry, discover
from oneframe.runtimes.manager import InstallRefused, Runtimes, find_uv
from oneframe.scheduler import for_root
from oneframe.scratch import Scratch

LOGGER = logging.getLogger("oneframe.server")
# Answered by the loop that reads requests, never behind another one: reads that answer at once.
AT_ONCE = frozenset({"engine.hello", "values.locate"})
# Each stop, answered at once too, and the requests of its kind it takes off the queue first.
STOPS: dict[str, frozenset[str]] = {
    "run.stop": frozenset({"graph.run"}),
    "runtimes.stop": frozenset({"runtimes.install"}),
    "models.stop": frozenset({"models.download", "models.verify"}),
}


def _bad_params(message: str) -> Failure:
    return Failure(message, "bad_params", kind="request")


def _required(params: dict[str, Any], name: str) -> Any:
    """A parameter the method cannot do without: its absence is the request's fault, not a bug."""
    if params.get(name) is None:
        raise _bad_params(f"The request needs {name!r}.")
    return params[name]


class Engine:
    def __init__(
        self,
        data: Path,
        node_roots: list[Path],
        say: Emit,
        runtime_roots: list[Path] | None = None,
        uv: str | None = None,
        uv_home: Path | None = None,
        profile: Callable[[], dict[str, Any]] | None = None,
        packaged: bool = False,
        scratch: Scratch | None = None,
        warnings: list[str] | None = None,
        transfer: dict[str, Any] | None = None,
        graph_roots: list[Path] | None = None,
    ):
        """`warnings`: the root's, when the caller has already claimed it (layout.open_root).
        `transfer`: options for each download (a test's TLS context, policy or proxy).
        `graph_roots`: the folders of graphs that ship (default: the repo's graphs/)."""
        self.data = data
        self.layout = Layout(data)
        # Before anything is written under the root: a newer layout stops the engine here.
        self.warnings = warnings if warnings is not None else claim_root(data, packaged, __version__)
        # This process's own folder under cache/tmp/; the start sweeps only dead processes' folders.
        self.scratch = scratch or Scratch.claim(self.layout)
        self.node_roots = node_roots
        self.graph_roots = graph_roots if graph_roots is not None else [BUILTIN_GRAPHS_DIR]
        self.say = say
        self.registry: Registry = discover(node_roots)
        self.cache = Cache(self.layout.objects, self.scratch.folder / "runs")
        self.runtimes = Runtimes(
            data, runtime_roots, uv=find_uv(uv), profile=profile, uv_home=uv_home, transfer=transfer
        )
        self.learned = LearnedStore(data)
        self.models = Jobs(data, say, transfer, in_use=lambda: in_use(self.layout))
        # A fit reads the machine fresh, since free memory changes; a test passes its own profile.
        self._profile = profile
        self.scheduler = for_root(
            data,
            self.registry,
            self.runtimes,
            self.cache,
            machine=self._machine,
            settings=lambda: read_settings(data),
            learned=self.learned,
            tmp_root=self.scratch.folder,
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
            "graph.fits": self.graph_fits,
            "graph.run": self.graph_run,
            "graphs.list": self.graphs_list,
            "graphs.get": self.graphs_get,
            "graphs.save": self.graphs_save,
            "graphs.remove": self.graphs_remove,
            "inputs.import": self.inputs_import,
            "values.locate": self.values_locate,
            "run.stop": self.run_stop,
            "runtimes.list": self.runtimes_list,
            "runtimes.plan": self.runtimes_plan,
            "runtimes.install": self.runtimes_install,
            "runtimes.stop": self.runtimes_stop,
            "runtimes.remove": self.runtimes_remove,
            "models.download": self.models_download,
            "models.verify": self.models_verify,
            "models.stop": self.models_stop,
            "models.list": self.models_list,
            "models.remove": self.models_remove,
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
        node = str(_required(params, "node"))
        if node not in self.registry.nodes:
            raise _bad_params(f"No node called {node!r} is installed.")
        manifest = self.registry.get(node)
        given = dict(params.get("params") or {})
        values, problems = step_params(manifest, given, self.layout.inputs)
        if problems:
            raise _bad_params("; ".join(problems))
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
        the_plan = plan(Graph.from_json(_required(params, "graph")), self.registry, self.layout.inputs)
        return {"order": the_plan.order, "notes": the_plan.notes, "depends": the_plan.depends}

    def graph_fits(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{graph, port}`: what `port` (`step.port`) could connect to, in the graph and by adding a
        node, each with how (graph.fits)."""
        graph = Graph.from_json(_required(params, "graph"))
        return fits(graph, str(_required(params, "port")), self.registry)

    def _graphs(self) -> graphs.Listing:
        return graphs.discover(self.graph_roots, self.layout.graphs)

    def graphs_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Every graph that ships and every one the person saved, by name, and each broken file."""
        return self._graphs().to_json()

    def graphs_get(self, params: dict[str, Any]) -> dict[str, Any]:
        found = self._graphs().get(str(_required(params, "name")))
        return {**found.summary(), "graph": found.graph.to_json()}

    def graphs_save(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{name, graph}`: the person's graph, written under the data root; a shipped one of that
        name is left as it is, and shadowed."""
        graphs.save(self.layout.graphs, _required(params, "name"), _required(params, "graph"))
        return self._graphs().get(str(params["name"])).summary()

    def graphs_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{name}`: the person's own graph, deleted because they asked for it by name. A shipped
        one of that name is then the one listed (`now`)."""
        name = _required(params, "name")
        graphs.remove(self.layout.graphs, name)
        now = self._graphs().graphs.get(str(name))
        return {"name": name, "removed": True, "now": now.summary() if now is not None else None}

    def inputs_import(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{path}`: copy a file into the root's inputs/, by its content; its id and its name. Only
        the main process calls it, with the file the person picked: it is not on the page's list."""
        source = Path(str(_required(params, "path")))
        if not source.is_absolute() or not source.is_file():
            raise _bad_params(f"No file at {source}.")
        ident, name = inputs.import_file(self.layout, source)
        return {"id": ident, "name": name}

    def values_locate(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{key, port}`: the file of a value a run committed to the cache, its type and carrier,
        for the app's value protocol; anything no committed entry has is refused. Only the main
        process calls it: it answers a path, so it is not on the page's list."""
        key, port = _required(params, "key"), _required(params, "port")
        outputs = self.cache.get(key) if is_sha256(key) else None
        value = outputs.get(port) if outputs is not None and isinstance(port, str) else None
        if value is None:
            raise _bad_params(f"No value {port!r} was committed under the key {key!r}.")
        found = ports.types().get(value.type)
        return {"path": str(value.path), "type": value.type, "carrier": found.carrier if found else None}

    def graph_run(self, params: dict[str, Any]) -> dict[str, Any]:
        graph = Graph.from_json(_required(params, "graph"))
        # A bad graph is refused now, with its problems, not as an event.
        the_plan = plan(graph, self.registry, self.layout.inputs)
        used = {str(s.manifest.run.runtime) for s in the_plan.steps if s.manifest.run.where == "runtime"}
        run_id = uuid.uuid4().hex[:12]
        stop = threading.Event()

        def release() -> None:
            """The run slot, before the run's ending is sent, so a page that acts on hearing it is
            not refused; only while it is still this run's, since a next run may have taken it."""
            with self._lock:
                if self._run is not None and self._run[0] == run_id:
                    self._run = None

        def work() -> None:
            try:
                self.scheduler.run(graph, self.say, stop, run_id, release)
            except Exception:  # a bug, not a node failure: journalled, and said once the slot was free
                LOGGER.exception("run %s crashed", run_id)
            finally:
                release()

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
        runtime_id = str(_required(params, "runtime"))
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
        return {"stopping": self.runtimes.stop(str(_required(params, "runtime")))}

    def runtimes_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        runtime_id = str(_required(params, "runtime"))
        self._refuse_if_running(runtime_id, "removing")
        return self.runtimes.remove(runtime_id)

    def _node(self, params: dict[str, Any]) -> Any:
        node = str(_required(params, "node"))
        if node not in self.registry.nodes:
            raise _bad_params(f"No node called {node!r} is installed.")
        return self.registry.get(node)

    def models_download(self, params: dict[str, Any]) -> dict[str, Any]:
        """Fetch a node's files: `{node, checkpoints?}`, a list of checkpoints or "all". Answers at
        once with the job's id; model.* events follow."""
        return self.models.download(self._node(params), params.get("checkpoints"))

    def models_verify(self, params: dict[str, Any]) -> dict[str, Any]:
        """Hash again what the store holds: `{node?}`, one node's files or every file."""
        if params.get("node") is not None:
            return self.models.verify(str(params["node"]), [self._node(params)])
        return self.models.verify(None, list(self.registry.nodes.values()))

    def models_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        return self.models.stop(params.get("job"))

    def models_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """What the store holds for each node, and what no node pins; never uses the network."""
        return listing(self.models.store, self.registry)

    def models_remove(self, params: dict[str, Any]) -> dict[str, Any]:
        """`{node, checkpoint?}`: a node's files, or a checkpoint's; `{unpinned: [...]}`: exactly the
        files and partials models.list showed as pinned by no node."""
        busy = self.models.running()
        if params.get("unpinned") is not None:
            listed = params["unpinned"]
            if not isinstance(listed, list) or not all(isinstance(x, str) for x in listed):
                raise _bad_params("unpinned should be a list of what models.list showed.")
            return remove_unpinned(self.models.store, self.registry, self.node_roots, listed, busy)
        checkpoint = params.get("checkpoint")
        return remove_node(
            self.models.store,
            self.registry,
            self.node_roots,
            self._node(params),
            None if checkpoint is None else str(checkpoint),
            busy,
        )

    def shutdown(self, timeout: float = 30) -> None:
        """Stop a run, an install and a model job that are still going, and wait for them, so that
        no node process holding the card, no uv and no half-told download outlives the engine."""
        with self._lock:
            running = self._run
            run_thread = self._run_thread
        if running is not None:
            running[1].set()  # the executor sees it within a poll and kills the node's process
        installing = self.runtimes.installing()
        if installing is not None:
            self.runtimes.stop(installing)
        self.models.stop()
        deadline = time.monotonic() + timeout  # one budget for all, not one each
        if run_thread is not None:
            run_thread.join(max(deadline - time.monotonic(), 0))
        if self._install is not None:
            self._install.join(max(deadline - time.monotonic(), 0))
        self.models.shutdown(max(deadline - time.monotonic(), 0))
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


class Requests:
    """The requests the app sends, as the engine answers them: in order, by one worker thread, but
    for those AT_ONCE and the STOPS, which the caller's thread answers as they come."""

    def __init__(self, engine: Engine, say: Emit):
        self.engine = engine
        self.say = say
        self._queue: deque[dict[str, Any]] = deque()
        self._changed = threading.Condition()
        self._closed = False
        self._worker = threading.Thread(target=self._work, name="requests", daemon=True)
        self._worker.start()

    def take(self, message: dict[str, Any]) -> None:
        """One request: answered now if it is a stop or a read AT_ONCE, else queued."""
        method = str(message.get("method"))
        if method not in AT_ONCE and method not in STOPS:
            with self._changed:
                self._queue.append(message)
                self._changed.notify_all()
            return
        self._stop_queued(STOPS.get(method, frozenset()))
        reply = self.engine.handle(message)
        if reply is not None:
            self.say(reply)

    def _stop_queued(self, methods: frozenset[str] | None) -> None:
        """Each queued request of these methods (None: of any) answered as stopped, never begun."""
        with self._changed:
            gone = [m for m in self._queue if methods is None or m.get("method") in methods]
            for message in gone:
                self._queue.remove(message)
        for message in gone:
            self.say({"id": message.get("id"), "result": {"stopped": True}})

    def _work(self) -> None:
        while True:
            with self._changed:
                while not self._queue and not self._closed:
                    self._changed.wait()
                if not self._queue:
                    return
                message = self._queue.popleft()
            reply = self.engine.handle(message)
            if reply is not None:
                self.say(reply)

    def close(self, timeout: float = 30) -> None:
        """No more requests: each still queued is answered as stopped, and the one being answered
        is waited for, up to `timeout` seconds."""
        self._stop_queued(None)
        with self._changed:
            self._closed = True
            self._changed.notify_all()
        self._worker.join(timeout)


def main(argv: list[str] | None = None, transfer: dict[str, Any] | None = None) -> int:
    """The engine as the app starts it. `transfer`: options for each download, as Engine takes
    them, which only a test passes (fixtures/serve.py, to trust its CA)."""
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
        "--graphs",
        type=Path,
        action="append",
        default=None,
        help="a folder of graphs that ship; repeat for more (default: the repo's graphs/)",
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
        data, warnings = open_root(args.data, args.packaged, __version__)  # before anything is written
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
            warnings=warnings,
            transfer=transfer,
            graph_roots=args.graphs,
        )
    except RootRefused as exc:
        say({"event": "engine.failed", **exc.to_json()})
        return 2
    except Exception as exc:  # a root that cannot be written, a lock that cannot be taken: say why
        LOGGER.exception("the engine could not start")
        say(engine_crash(exc, event="engine.failed"))
        return 1
    say({"event": "engine.ready", **engine.hello({})})
    requests = Requests(engine, say)
    stdin = open(sys.stdin.fileno(), encoding="utf-8", errors="replace", closefd=False)  # noqa: SIM115
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            message = None
        if not isinstance(message, dict):
            say({"event": "engine.warning", "message": "A request was not a JSON object."})
            continue
        requests.take(message)
    requests.close()  # stdin closed: the app is quitting
    engine.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
