"""The far side of a node run: the script a runtime's Python starts by path.

    <runtime python> -P -u child.py <job.json>

A heavy node runs in its own runtime -- a separate interpreter with its own packages, where
nothing of the engine is installed. This script is therefore standard library only, and makes the
node API, alone, importable there: it registers a stub `oneframe` package whose path is empty, then
`oneframe.node` from the folder beside it, so node code can import `oneframe.node` and no other
engine module, even on the engine's own Python. `-P` keeps this file's folder off the import path:
it holds the engine's own modules (`models`, `graph`, `errors`, ...), and a node's `import models`
must find the runtime's package, never the engine's.

It then puts up the process's guards (oneframe/node/process.py) before any node code is imported,
runs the job (runner.py), and reports how it ended. Nothing imports this file: in the engine's
process the stub would replace the real `oneframe`, and the guards would close its downloads.

This file runs in every runtime's own Python, which may be older than the engine's: it is checked
for Python 3.11 (ruff.toml, pyrightconfig.json).
"""

from __future__ import annotations

import importlib.util
import json
import sys
import traceback
import types
from pathlib import Path

NODE_API = Path(__file__).resolve().parent / "node"


def _node_api() -> None:
    """`oneframe.node` importable, and nothing else of the engine."""
    stub = types.ModuleType("oneframe")
    stub.__path__ = []
    sys.modules["oneframe"] = stub
    spec = importlib.util.spec_from_file_location(
        "oneframe.node", NODE_API / "__init__.py", submodule_search_locations=[str(NODE_API)]
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load the node API from {NODE_API}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["oneframe.node"] = module
    spec.loader.exec_module(module)
    stub.node = module  # pyright: ignore[reportAttributeAccessIssue]


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    _node_api()
    from oneframe.node import failures, process, usage
    from oneframe.node.runner import run_job

    process.open_protocol()
    with Path(argv[0]).open(encoding="utf-8") as handle:
        job = json.load(handle)
    if job.get("exit_with_parent"):
        process.exit_with_parent()
    this = process.Process()
    try:
        if not job.get("allow_network"):
            process.forbid_network()
        process.report_tqdm(process.emit, this.stage)
        process.emit(run_job(job, process.emit, process=this))
        return 0
    except BaseException as exc:  # the engine decides what it means; report everything
        process.emit(
            {
                "event": "error",
                "kind": failures.classify(exc),
                "stage": this.stage.get("stage"),
                "message": failures.describe(exc),
                "trace": traceback.format_exc()[-4000:],
                **(this.peaks or usage.peaks(str(job.get("device") or ""), this.ram_start)),
            }
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
