"""What the footprint test checks (spec 014), each as a function a test can show failing: folder
snapshots, the sandbox diff and the paths it allows, the real profile's watched folders, deep, and
the registry keys uv would touch.

The data root's exceptions are `outside` in contracts/layout.json. The sandbox diff allows a write
outside the root only at a path an entry names for what the engine's process tree runs; each path
is spelled from a variable of the environment or from the checkout, and expanded in the
environment it is checked in (the sandbox's, or the real one)."""

from __future__ import annotations

import json
import os
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
VARIABLES = (
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "XDG_CACHE_HOME",
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "TMPDIR",
)

# A path relative to the folder snapshotted: a file's size and modification time, or (-1, 0) for a
# folder. A folder's own time is left out: NTFS updates it in its parent's listing lazily, so it
# can change between two looks with nothing written, and what is in a folder speaks for it.
Snapshot = dict[str, tuple[int, int]]


def system() -> str:
    """This machine's system, by the name an entry gives it."""
    return "windows" if sys.platform == "win32" else sys.platform


def expand(spelled: str, env: Mapping[str, str], checkout: Path) -> Path | None:
    """An entry's path in an environment: `{NAME}/a/b` from the variable NAME, `{checkout}/a/b` from
    the repo. None when the variable is not set there, so the path does not apply; a variable not
    in VARIABLES fails, so a misspelt one never silently stops a check."""
    match = re.fullmatch(r"\{(\w+)\}((?:/[^/{}]+)*)", spelled)
    assert match, f"{spelled!r} is not spelled {{NAME}}/a/b"
    name, rest = match.groups()
    assert name == "checkout" or name in VARIABLES, f"{spelled!r} starts from an unknown variable"
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
    """Everything under `folder`, by its path relative to it. Read with scandir, whose entries carry
    their size and time on Windows: a uv cache holds hundreds of thousands of files."""
    found: Snapshot = {}
    start = len(str(folder).rstrip("\\/")) + 1  # the folder and one separator, even for C:\
    pending = [str(folder)]
    while pending:
        try:
            with os.scandir(pending.pop()) as entries:
                listed = list(entries)
        except OSError:
            continue  # gone while it was looked at
        for entry in listed:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
                info = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            found[entry.path[start:]] = (-1, 0) if is_dir else (info.st_size, info.st_mtime_ns)
            if is_dir:
                pending.append(entry.path)
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


# The real profile's folders the test watches: the default folders of what the engine's process
# tree runs, each with its program. The engine points each elsewhere, so a write here is one it
# missed. A new name elsewhere in the profile is another program's (a runner's Firefox, once).
WATCHED: list[dict[str, Any]] = [
    {"program": "uv", "what": "cache", "systems": ["windows"], "paths": ["{LOCALAPPDATA}/uv"]},
    {
        "program": "uv",
        "what": "cache",
        "systems": ["linux"],
        "paths": ["{XDG_CACHE_HOME}/uv", "{HOME}/.cache/uv"],
    },
    {
        "program": "uv",
        "what": "Pythons, tools and settings",
        "systems": ["windows"],
        "paths": ["{APPDATA}/uv"],
    },
    {
        "program": "uv",
        "what": "Pythons and tools",
        "systems": ["linux"],
        "paths": ["{XDG_DATA_HOME}/uv", "{HOME}/.local/share/uv"],
    },
    {
        "program": "uv",
        "what": "settings",
        "systems": ["linux"],
        "paths": ["{XDG_CONFIG_HOME}/uv", "{HOME}/.config/uv"],
    },
    {"program": "uv", "what": "executables", "systems": ["windows"], "paths": ["{USERPROFILE}/.local/bin"]},
    {"program": "uv", "what": "executables", "systems": ["linux"], "paths": ["{HOME}/.local/bin"]},
    {"program": "CPython", "what": "per-user folder", "systems": ["windows"], "paths": ["{APPDATA}/Python"]},
    {"program": "CPython", "what": "per-user folder", "systems": ["linux"], "paths": ["{HOME}/.local/lib"]},
]

# Each watched folder's snapshot, or None while it does not exist.
Profile = dict[str, Snapshot | None]


def watched(entries: Iterable[dict[str, Any]], env: Mapping[str, str], on: str) -> list[Path]:
    """The folders the entries watch on this system, in this environment."""
    found = []
    for entry in entries:
        if on in entry["systems"]:
            found += [p for spelled in entry["paths"] if (p := expand(spelled, env, Path())) is not None]
    return sorted(set(found))


def profile(folders: Iterable[Path]) -> Profile:
    """Each folder, deep, or None if it is not there."""
    return {str(folder): snapshot(folder) if folder.is_dir() else None for folder in folders}


def profile_changes(before: Profile, after: Profile) -> list[str]:
    """Anything new, changed or gone under a watched folder, and a watched folder that appeared or
    went."""
    found = []
    for folder, then in before.items():
        now = after.get(folder)
        if then is None or now is None:
            if then != now:
                found.append(folder)
            continue
        found += [
            str(Path(folder, name))
            for name in sorted(then.keys() | now.keys())
            if then.get(name) != now.get(name)
        ]
    return found


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
