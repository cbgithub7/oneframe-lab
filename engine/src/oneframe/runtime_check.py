"""Each runtime's code checked at the Python it runs on (spec 005).

    npm run runtime:check -- [--every-runtime] [--data <root>]
        [--nodes <folder>]... [--runtimes <folder>]...

A runtime may run an older Python than the engine, so code in a runtime's folder, or in the folder
of a node that runs in one, is checked for that Python, never the engine's. The tool reads the
runtime definitions and the node manifests, and:

- holds each such folder to a `ruff.toml` that extends the repo's and sets `target-version` to its
  runtime's Python, so ruff, and the hooks after each edit, lint and format it for that Python;
- runs pyright once per runtime installed here over its folder and its nodes, with that runtime's
  interpreter (its packages, its sources and stand-ins) at its Python, and once over the engine's
  nodes with the engine's own. Each configuration is written in the tool's scratch under the data
  root; pyright keeps its own cache where `npm run engine:check`'s does.

A runtime not installed here is named and left unchecked, or, with `--every-runtime` (CI's models
job, which installs them all), is a failure. Nothing here names a runtime or a node.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from oneframe import BUILTIN_NODES_DIR, ENGINE_DIR, REPO_ROOT, __version__, processes
from oneframe.layout import Layout, RootRefused, open_root
from oneframe.registry import Registry, discover
from oneframe.runtimes.definition import RuntimeSet
from oneframe.runtimes.manager import RuntimeMissing, Runtimes, find_uv
from oneframe.scratch import for_tool

ROOT_RUFF = REPO_ROOT / "ruff.toml"
ENGINE_PYTHON = f"{sys.version_info.major}.{sys.version_info.minor}"


@dataclass(frozen=True)
class Group:
    """Code that runs on one Python: the engine's nodes, or a runtime's folder and its nodes."""

    name: str  # "engine", or the runtime's id
    python: str  # "3.14"
    interpreter: Path | None  # None: not installed here, and `why` says so
    folders: tuple[Path, ...]
    runtime: bool
    why: str = ""


def groups(runtimes: RuntimeSet, registry: Registry, interpreter_for: Callable[[str], Path]) -> list[Group]:
    """The engine's nodes, then each runtime with the nodes that run in it."""
    manifests = sorted(registry.nodes.values(), key=lambda m: m.id)
    found = [
        Group(
            "engine",
            ENGINE_PYTHON,
            Path(sys.executable),
            tuple(m.folder for m in manifests if m.run.where == "engine"),
            runtime=False,
        )
    ]
    for runtime in sorted(runtimes.runtimes.values(), key=lambda r: r.id):
        nodes = [m.folder for m in manifests if m.run.where == "runtime" and m.run.runtime == runtime.id]
        try:
            interpreter, why = interpreter_for(runtime.id), ""
        except RuntimeMissing as exc:
            interpreter, why = None, str(exc)
        found.append(
            Group(runtime.id, runtime.python, interpreter, (runtime.folder, *nodes), runtime=True, why=why)
        )
    return found


def target_for(python: str) -> str:
    """ruff's name for a Python: "3.14" is "py314"."""
    return "py" + python.replace(".", "")


def ruff_problems(found: list[Group], root_config: Path = ROOT_RUFF) -> list[str]:
    """Each runtime folder, and each runtime node's folder, whose `ruff.toml` is missing, does not
    extend the repo's, or targets another Python than its runtime's."""
    problems = []
    for group in found:
        if not group.runtime:
            continue
        want = target_for(group.python)
        for folder in group.folders:
            config = folder / "ruff.toml"
            where = f"{folder} (runtime {group.name}, Python {group.python})"
            if not config.is_file():
                problems.append(
                    f"{where}: no ruff.toml; it should extend the repo's and set target-version = {want!r}"
                )
                continue
            try:
                data = tomllib.loads(config.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
                problems.append(f"{where}: ruff.toml cannot be read: {exc}")
                continue
            extend = data.get("extend")
            if not isinstance(extend, str) or (folder / extend).resolve() != root_config.resolve():
                problems.append(f"{where}: ruff.toml does not extend the repo's ({root_config})")
            if data.get("target-version") != want:
                problems.append(
                    f"{where}: ruff.toml targets {data.get('target-version')!r}; its runtime runs {want!r}"
                )
    return problems


def pyright_config(group: Group) -> dict[str, object]:
    """pyright's settings for one group: its Python, and the node API (`oneframe.node`) beside its
    code, as the child puts it on the path. Its folders go on pyright's command line, since a
    configuration's `include` takes no absolute path."""
    return {
        "pythonVersion": group.python,
        "typeCheckingMode": "standard",
        "extraPaths": [str(ENGINE_DIR.parent)],
    }


def check_with_pyright(group: Group, folder: Path, say: Callable[[str], None] = print) -> bool:
    """pyright over one group, with its interpreter; True when it found no error."""
    assert group.interpreter is not None
    config = folder / f"pyright-{group.name}.json"
    config.write_text(json.dumps(pyright_config(group), indent=2), encoding="utf-8")
    argv = [sys.executable, "-m", "pyright", "-p", str(config), "--pythonpath", str(group.interpreter)]
    argv += [str(folder) for folder in group.folders]
    code, _ = processes.run(argv, env=dict(os.environ), on_line=say, stop=threading.Event())
    return code == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runtime_check", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--every-runtime", action="store_true", help="a runtime not installed fails")
    parser.add_argument("--data", type=Path, default=None, help="data root (default: the dev root)")
    parser.add_argument(
        "--nodes", type=Path, action="append", default=None, help="node folders (default: nodes/)"
    )
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="runtime definitions")
    parser.add_argument("--uv", default=None)
    args = parser.parse_args(argv)

    try:
        data, warnings = open_root(args.data.resolve() if args.data else None, version=__version__)
    except RootRefused as exc:
        print(exc, file=sys.stderr)
        return 1
    for warning in warnings:
        print(warning, file=sys.stderr)
    manager = Runtimes(data.resolve(), args.runtimes, uv=find_uv(args.uv))
    definitions = manager.definitions()
    registry = discover(args.nodes or [BUILTIN_NODES_DIR])
    found = groups(definitions, registry, manager.python_for)
    failed = False
    for problem in ruff_problems(found):
        print(problem, file=sys.stderr)
        failed = True
    with for_tool(Layout(data)) as mine:
        folder = mine.make("runtime-check-")
        for group in found:
            if not group.folders:
                continue
            if group.interpreter is None:
                print(f"{group.name}: not checked with pyright: {group.why}", file=sys.stderr)
                failed = failed or args.every_runtime
                continue
            print(f"{group.name}: pyright at Python {group.python}, {len(group.folders)} folder(s)")
            if not check_with_pyright(group, folder):
                failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
