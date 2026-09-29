"""The engine as the app sees it: one long-lived process speaking NDJSON on stdin and stdout.

    python -m oneframe.server --data <data root>

Requests are `{"id": 1, "method": "nodes.list", "params": {}}` and are answered by
`{"id": 1, "result": ...}` or `{"id": 1, "error": {"message": ..., "problems": [...]}}`. Anything
that takes time (a graph run) answers at once with a run id, and its progress arrives as events,
`{"event": "node.done", ...}`, with no id. There is no polling and no local network port: the only
way in is this process's own stdin, which only the app holds.

Only one graph runs at a time: they share one GPU, and a second run would only fight the first for
memory. A run asked for while another is going is refused with the running one's id.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
import traceback
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from oneframe import BUILTIN_NODES_DIR, __version__, ports
from oneframe.cache import Cache
from oneframe.graph import Graph, GraphError, plan
from oneframe.registry import Registry, discover
from oneframe.scheduler import Scheduler

LOGGER = logging.getLogger("oneframe.server")


class Engine:
    def __init__(self, data: Path, node_roots: list[Path], say: Callable[[dict[str, Any]], None]):
        self.data = data
        self.node_roots = node_roots
        self.say = say
        self.registry: Registry = discover(node_roots)
        self.cache = Cache(data / "cache")
        self.cache.clear_tmp()
        self.scheduler = Scheduler(
            self.registry, self.cache, models_dir=data / "models", log_dir=data / "logs"
        )
        self._run: tuple[str, threading.Event] | None = None
        self._lock = threading.Lock()
        self.methods: dict[str, Callable[[dict[str, Any]], Any]] = {
            "engine.hello": self.hello,
            "nodes.list": self.nodes_list,
            "nodes.reload": self.nodes_reload,
            "ports.list": self.ports_list,
            "graph.validate": self.graph_validate,
            "graph.run": self.graph_run,
            "run.stop": self.run_stop,
        }

    def hello(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "engine": __version__,
            "python": sys.version.split()[0],
            "data": str(self.data),
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
        plan(graph, self.registry)  # refuse a bad graph now, with its problems, not as an event
        with self._lock:
            if self._run is not None:
                raise RuntimeError(f"Run {self._run[0]} is still going; stop it first.")
            run_id = uuid.uuid4().hex[:12]
            stop = threading.Event()
            self._run = (run_id, stop)

        def work() -> None:
            try:
                self.scheduler.run(graph, self.say, stop.is_set, run_id)
            except Exception as exc:  # a bug, not a node failure: say so rather than die silently
                LOGGER.exception("run %s crashed", run_id)
                self.say(
                    {
                        "event": "run.failed",
                        "run": run_id,
                        "kind": "engine",
                        "message": str(exc),
                        "detail": traceback.format_exc()[-4000:],
                    }
                )
            finally:
                with self._lock:
                    self._run = None

        threading.Thread(target=work, name=f"run-{run_id}", daemon=True).start()
        return {"run": run_id}

    def run_stop(self, params: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            if self._run is None or (params.get("run") and params["run"] != self._run[0]):
                return {"stopping": False}
            self._run[1].set()
            return {"stopping": True, "run": self._run[0]}

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        req_id = message.get("id")
        method = message.get("method")
        fn = self.methods.get(str(method))
        if fn is None:
            return {"id": req_id, "error": {"message": f"Unknown method {method!r}."}}
        try:
            return {"id": req_id, "result": fn(dict(message.get("params") or {}))}
        except GraphError as exc:
            return {"id": req_id, "error": {"message": "The graph cannot run.", "problems": exc.problems}}
        except Exception as exc:
            LOGGER.exception("%s failed", method)
            return {"id": req_id, "error": {"message": f"{type(exc).__name__}: {exc}"}}


def default_data_dir() -> Path:
    if os.environ.get("ONEFRAME_DATA"):
        return Path(os.environ["ONEFRAME_DATA"])
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "OneframeLab"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "oneframe-lab"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="oneframe.server")
    parser.add_argument("--data", type=Path, default=None, help="data root (cache, models, logs)")
    parser.add_argument(
        "--nodes",
        type=Path,
        action="append",
        default=None,
        help="a folder of nodes; repeat for more (default: the built-in nodes)",
    )
    args = parser.parse_args(argv)
    data = args.data or default_data_dir()
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

    engine = Engine(data, args.nodes or [BUILTIN_NODES_DIR], say)
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
