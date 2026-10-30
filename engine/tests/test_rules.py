"""AGENTS.md's rules that a test can hold (spec 013): nothing in the engine names a runtime, a node or
the tests."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import oneframe

TESTS = Path(__file__).resolve().parent
REPO = TESTS.parents[1]
ENGINE = Path(oneframe.__file__).resolve().parent

# A library named like a runtime, where the engine may use it: (module, where it stands, the name).
# Each stands for the library, never the runtime; a new use fails until it is listed here.
LIBRARY_NAMES = {
    ("node/usage.py", "an import", "torch"),  # the cap and the peaks, from the runtime's own torch
    ("node/usage.py", "a sys.modules key", "torch"),
    ("node/context.py", "a sys.modules key", "torch"),  # memory_free_mb, only if torch is loaded
    ("environment.py", "a value of LIBRARY_CACHES", "torch"),  # TORCH_HOME's folder under the root
    ("runtimes/definition.py", "a comparison", "torch"),  # a lock's package names, for the floor
    ("bench_runtime.py", "a .startswith() argument", "torch"),  # the report's torch lines of the freeze
}


def _repo_ids() -> set[str]:
    """Every runtime's and node's id in the repo, the tests' own included."""
    files = [
        *(REPO / "runtimes").glob("*/runtime.json"),
        *(TESTS / "runtimes").glob("*/runtime.json"),
        *(REPO / "nodes").glob("*/node.json"),
        *(TESTS / "hardware").glob("*/node.json"),
    ]
    return {json.loads(f.read_text(encoding="utf-8"))["id"] for f in files}


def _docstrings(tree: ast.Module) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _where(node: ast.AST, parents: dict[int, ast.AST]) -> str:
    """Where a string stands, as LIBRARY_NAMES says it."""
    parent = parents.get(id(node))
    if isinstance(parent, ast.Compare):
        return "a comparison"
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute):
        owner = ast.unparse(parent.func.value)
        if owner == "sys.modules":
            return "a sys.modules key"
        return f"a .{parent.func.attr}() argument"
    if isinstance(parent, ast.Subscript) and ast.unparse(parent.value) == "sys.modules":
        return "a sys.modules key"
    if isinstance(parent, ast.Dict) and node in parent.values:
        target = parents.get(id(parent))
        if isinstance(target, ast.Assign | ast.AnnAssign):
            names = target.targets if isinstance(target, ast.Assign) else [target.target]
            return f"a value of {', '.join(ast.unparse(n) for n in names)}"
    return "a value"


def _names(text: str, ids: set[str]) -> set[str]:
    """The ids `text` holds: all of it, or a part of it as a path."""
    return ids & {text, *re.split(r"[/\\]", text)}


def _engine_modules() -> dict[str, str]:
    return {
        p.relative_to(ENGINE).as_posix(): p.read_text(encoding="utf-8") for p in sorted(ENGINE.rglob("*.py"))
    }


def _findings(modules: dict[str, str], ids: set[str]) -> tuple[list[str], set[tuple[str, str, str]]]:
    """Each place a module names an id or `tests`, but for LIBRARY_NAMES; and which of those were
    found."""
    found: list[tuple[str, int, str]] = []
    used: set[tuple[str, str, str]] = set()
    for module, source in modules.items():
        tree = ast.parse(source)
        docstrings = _docstrings(tree)
        parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        for node in ast.walk(tree):
            held: list[tuple[str, str]] = []
            if isinstance(node, ast.Import | ast.ImportFrom):
                names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                held = [("an import", name) for m in names for name in ids & {m, m.split(".")[0]}]
            elif (
                isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
            ):
                where = _where(node, parents)
                held = [(where, name) for name in _names(node.value, ids | {"tests"})]
            for where, name in held:
                if (module, where, name) in LIBRARY_NAMES:
                    used.add((module, where, name))
                else:
                    found.append((module, getattr(node, "lineno", 0), f"{where} holds {name!r}"))
    return [f"{module}:{line}: {what}" for module, line, what in sorted(found)], used


def test_no_engine_module_names_a_runtime_a_node_or_the_tests() -> None:
    ids = _repo_ids()
    assert {"torch", "test.vram"} <= ids  # the ids it looks for are found
    found, used = _findings(_engine_modules(), ids)
    assert found == [], "\n".join(found)
    # Every exception is still needed, so the list stays true.
    assert used == LIBRARY_NAMES, LIBRARY_NAMES - used


def test_a_listed_module_that_gains_a_runtime_id_is_still_caught() -> None:
    """Exceptions are keyed by where a name stands, never by file alone."""
    module = "environment.py"
    source = _engine_modules()[module]
    added = 'NOT_LISTED = {"runtime": "torch"}\nHARDWARE = Path("engine") / "tests/hardware"\nimport torch\n'
    found, used = _findings({module: source + added}, {"torch"})
    lines = len(source.splitlines())
    assert found == [
        f"{module}:{lines + 1}: a value of NOT_LISTED holds 'torch'",
        f"{module}:{lines + 2}: a value holds 'tests'",
        f"{module}:{lines + 3}: an import holds 'torch'",
    ]
    assert used == {(module, "a value of LIBRARY_CACHES", "torch")}
