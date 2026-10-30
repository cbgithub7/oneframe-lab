"""What the footprint test checks (spec 014), each as a function a test can show failing: folder
snapshots, the sandbox diff and the paths it allows, the real profile's top-level names, and the
registry keys uv would touch.

The data root's exceptions are `outside` in contracts/layout.json. The sandbox diff allows a write
outside the root only at a path an entry names for what the engine's process tree runs; each path
is spelled from a variable of the environment or from the checkout, and expanded in the
environment it is checked in (the sandbox's, or the real one)."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from oneframe import CONTRACTS_DIR

OUTSIDE: list[dict[str, Any]] = json.loads((CONTRACTS_DIR / "layout.json").read_text(encoding="utf-8"))[
    "outside"
]
WRITERS = ("app", "engine", "runtime", "tool", "dev checkout", "graphics driver")
ENGINE_TREE = ("engine", "runtime", "graphics driver")  # what the engine's process tree runs
SYSTEMS = ("windows", "linux")
# The variables an entry's path may start from, besides {checkout}.
VARIABLES = ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "XDG_CACHE_HOME", "XDG_DATA_HOME", "TMPDIR")

# A path relative to the folder snapshotted: its size (-1 for a folder) and modification time.
Snapshot = dict[str, tuple[int, int]]


def system() -> str:
    """This machine's system, by the name an entry gives it."""
    return "windows" if sys.platform == "win32" else sys.platform


def expand(spelled: str, env: Mapping[str, str], checkout: Path) -> Path | None:
    """An entry's path in an environment: `{NAME}/a/b` from the variable NAME, `{checkout}/a/b` from
    the repo. None when the variable is not set there, so the path does not apply."""
    match = re.fullmatch(r"\{(\w+)\}((?:/[^/{}]+)*)", spelled)
    assert match, f"{spelled!r} is not spelled {{NAME}}/a/b"
    name, rest = match.groups()
    if name != "checkout" and name not in VARIABLES:
        return None
    base = str(checkout) if name == "checkout" else env.get(name)
    return Path(base, *rest.split("/")[1:]) if base else None


def allowed(
    entries: Iterable[dict[str, Any]], writers: Iterable[str], env: Mapping[str, str], checkout: Path, on: str
) -> list[Path]:
    """The paths the entries name, on this system, for these writers, in this environment."""
    writers = set(writers)
    found = []
    for entry in entries:
        if entry["writer"] in writers and on in entry["systems"]:
            found += [p for spelled in entry["paths"] if (p := expand(spelled, env, checkout)) is not None]
    return found


def snapshot(folder: Path) -> Snapshot:
    """Everything under `folder`, by its path relative to it."""
    found: Snapshot = {}
    for path in folder.rglob("*"):
        try:
            info = path.stat()
        except OSError:
            continue  # gone while it was looked at
        found[str(path.relative_to(folder))] = (-1 if path.is_dir() else info.st_size, info.st_mtime_ns)
    return found


def _within(path: Path, base: Path) -> bool:
    return path == base or path.is_relative_to(base)


def written_outside(
    before: Snapshot, after: Snapshot, folder: Path, root: Path, allowed: list[Path]
) -> list[str]:
    """What is new in `folder` (a sandbox) outside `root`, other than at an allowed path or a folder
    on the way to one (a cache's parent made with it)."""
    found = []
    for name in sorted(after.keys() - before.keys()):
        path = folder / name
        if _within(path, root) or any(_within(path, p) for p in allowed):
            continue
        if after[name][0] == -1 and any(_within(p, path) for p in allowed):
            continue
        found.append(name)
    return found


def top_level(folders: Iterable[str | None]) -> dict[str, set[str]]:
    """The names directly in each of these folders that exists."""
    found: dict[str, set[str]] = {}
    for folder in folders:
        if folder and Path(folder).is_dir():
            found[folder] = {p.name for p in Path(folder).iterdir()}
    return found


def new_top_level(before: dict[str, set[str]], after: dict[str, set[str]]) -> list[str]:
    """Names that appeared directly in a folder of `before`."""
    return sorted(
        str(Path(folder, name))
        for folder, names in before.items()
        for name in after.get(folder, set()) - names
    )


def registry() -> dict[str, Any]:
    """HKCU\\Software\\Python, every key and value below it, and HKCU\\Environment's Path: what uv
    changes when it registers a Python or puts its bin folder on the path. Empty off Windows."""
    found: dict[str, Any] = {}
    if sys.platform != "win32":
        return found
    import winreg

    def walk(path: str) -> None:
        try:
            handle = winreg.OpenKey(winreg.HKEY_CURRENT_USER, path)
        except OSError:
            return
        with handle:
            index = 0
            while True:
                try:
                    name, value, _kind = winreg.EnumValue(handle, index)
                except OSError:
                    break
                found[f"{path}:{name}"] = value
                index += 1
            index = 0
            children = []
            while True:
                try:
                    children.append(winreg.EnumKey(handle, index))
                except OSError:
                    break
                index += 1
        found[path] = sorted(children)
        for child in children:
            walk(f"{path}\\{child}")

    walk(r"Software\Python")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as handle:
            found["Environment:Path"] = winreg.QueryValueEx(handle, "Path")[0]
    except OSError:
        found["Environment:Path"] = None
    return found


def registry_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """The keys and values added, removed or changed."""
    missing = object()
    return sorted(k for k in before.keys() | after.keys() if before.get(k, missing) != after.get(k, missing))
