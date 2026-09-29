"""Runtimes: the Python environments that heavy nodes run in, one per node family.

A runtime is data, a folder the engine finds by looking (`runtimes/<id>/` in the repo):

    pyproject.toml   a virtual uv project; each build (cpu, cu126, cu130, ...) is an extra, and
                     the extras conflict, so one lock holds every build's packages
    uv.lock          committed; what `uv sync --frozen` installs, exactly
    runtime.json     the builds and what each needs from the machine, the Python, compiled
                     extensions and their class, pinned upstream sources, environment variables

Nothing in the engine names a runtime. A node names the runtime it runs in (`run.runtime` in its
manifest), and the scheduler asks this module for that runtime's interpreter. A broken definition
is kept as a problem to show beside the others, never an error that hides them.

This module reads and checks definitions; plan() picks the build for a machine. Both are pure: no
process is started and nothing is fetched, so listing and planning never touch the network.
"""

from __future__ import annotations

import hashlib
import json
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe import RUNTIMES_DIR

DEFINITION = "runtime.json"
PYPROJECT = "pyproject.toml"
LOCK = "uv.lock"
VENDORS = ("nvidia", "amd", "intel", "none")
OSES = ("windows", "linux", "macos")
EXTENSION_CLASSES = ("stand-in", "optional", "required")
# torch.load on a crafted file could run code before 2.6, weights_only or not (CVE-2025-32434).
TORCH_FLOOR = (2, 6)

_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_BUILD = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_VERSION = re.compile(r"^\d+(?:\.\d+)*$")
_PYTHON = re.compile(r"^3\.\d+(?:\.\d+)?$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class DefinitionError(ValueError):
    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


def version_tuple(text: str) -> tuple[int, ...]:
    """'560.94' -> (560, 94). A local label ('2.6.0+cu126') is dropped. Raises on anything else."""
    base = text.split("+", 1)[0].strip()
    if not _VERSION.match(base):
        raise ValueError(f"{text!r} is not a version")
    return tuple(int(part) for part in base.split("."))


@dataclass(frozen=True)
class Build:
    """One build of a runtime: an extra in its pyproject, and what it needs from the machine."""

    name: str
    vendor: str  # nvidia | amd | intel | none (runs on the processor)
    min_capability: str | None = None
    max_capability: str | None = None  # "12" means any 12.x
    min_driver: str | dict[str, str] | None = None  # one floor, or one per OS
    os: tuple[str, ...] = ()  # empty: any
    disk_mb: int | None = None

    def driver_floor(self, os_name: str) -> str | None:
        if isinstance(self.min_driver, dict):
            return self.min_driver.get(os_name)
        return self.min_driver


@dataclass(frozen=True)
class Extension:
    """A compiled package. stand-in: a pure-Python replacement ships in the definition folder.
    optional: left out, and the nodes do without it. required: needs a compiler, so the runtime is
    blocked with the reason (prebuilt wheels come later, per runtime)."""

    package: str
    kind: str  # "class" in runtime.json
    purpose: str  # "for" in runtime.json
    why: str = ""
    needs: str = ""
    stand_in: str = ""


@dataclass(frozen=True)
class Source:
    """Upstream code at a pinned archive, checked by sha256 and made importable."""

    name: str
    url: str
    sha256: str
    paths: tuple[str, ...] = (".",)


@dataclass(frozen=True)
class RuntimeDef:
    id: str
    title: str
    python: str
    builds: tuple[Build, ...]
    folder: Path
    summary: str = ""
    extensions: tuple[Extension, ...] = ()
    sources: tuple[Source, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    probe: str | None = None  # "probe.py:run", for the runtime report
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    def build(self, name: str) -> Build | None:
        return next((b for b in self.builds if b.name == name), None)

    @property
    def definition_path(self) -> Path:
        return self.folder / DEFINITION

    @property
    def lock_path(self) -> Path:
        return self.folder / LOCK

    def hashes(self) -> dict[str, str]:
        """What an installed environment was built from. Read now, not when the definition was
        loaded, so a lock changed under a running engine makes the runtime out of date at once."""
        return {
            "lock_sha256": text_sha256(self.lock_path),
            "definition_sha256": text_sha256(self.definition_path),
        }

    def to_json(self) -> dict[str, Any]:
        return dict(self.raw, folder=str(self.folder))


def text_sha256(path: Path) -> str:
    """The sha256 of a text file with its line endings made LF, so a checkout that turned them into
    CRLF does not make an installed runtime look out of date."""
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def _text(data: dict[str, Any], key: str, problems: list[str], required: bool = True) -> str:
    value = data.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or not value.strip():
        problems.append(f"{key} should be a non-empty string")
        return ""
    return value


def _version_field(row: dict[str, Any], key: str, where: str, problems: list[str]) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not _VERSION.match(value):
        problems.append(f'{where}.{key} should be a version like "7.5"')
        return None
    return value


def _builds(rows: Any, problems: list[str]) -> tuple[Build, ...]:
    if not isinstance(rows, list) or not rows:
        problems.append("builds should be a non-empty list, fastest first")
        return ()
    builds: list[Build] = []
    for i, row in enumerate(rows):
        where = f"builds[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where} should be an object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not _BUILD.match(name):
            problems.append(f"{where}.name should be lower case, like cu126 or cpu")
            continue
        where = f"builds.{name}"
        if any(b.name == name for b in builds):
            problems.append(f"{where} is listed twice")
            continue
        vendor = row.get("vendor")
        if vendor not in VENDORS:
            problems.append(f"{where}.vendor should be one of {', '.join(VENDORS)}")
            continue
        min_cap = _version_field(row, "min_capability", where, problems)
        max_cap = _version_field(row, "max_capability", where, problems)
        driver = row.get("min_driver")
        if driver is not None:
            if isinstance(driver, dict):
                bad = [
                    k
                    for k, v in driver.items()
                    if k not in OSES or not isinstance(v, str) or not _VERSION.match(v)
                ]
                if bad or not driver:
                    problems.append(f"{where}.min_driver per OS should map {', '.join(OSES)} to versions")
                    driver = None
            elif not isinstance(driver, str) or not _VERSION.match(driver):
                problems.append(f"{where}.min_driver should be a version, or one per OS")
                driver = None
        if vendor == "none" and (min_cap or max_cap or driver):
            problems.append(f"{where} runs on the processor, so it takes no capability or driver")
        oses = row.get("os") or []
        if not isinstance(oses, list) or any(o not in OSES for o in oses):
            problems.append(f"{where}.os should list some of {', '.join(OSES)}")
            oses = []
        disk = row.get("disk_mb")
        if disk is not None and (not isinstance(disk, int) or isinstance(disk, bool) or disk < 0):
            problems.append(f"{where}.disk_mb should be a whole number of MB")
            disk = None
        builds.append(Build(name, vendor, min_cap, max_cap, driver, tuple(oses), disk))
    if sum(1 for b in builds if b.vendor == "none") > 1:
        problems.append("builds: only one build can run on the processor")
    return tuple(builds)


def _extensions(rows: Any, folder: Path, problems: list[str]) -> tuple[Extension, ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        problems.append("extensions should be a list")
        return ()
    out: list[Extension] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("package"), str) or not row["package"]:
            problems.append(f"extensions[{i}] should be an object with a package")
            continue
        where = f"extensions.{row['package']}"
        kind = row.get("class")
        if kind not in EXTENSION_CLASSES:
            problems.append(f"{where}.class should be one of {', '.join(EXTENSION_CLASSES)}")
            continue
        purpose = row.get("for")
        if not isinstance(purpose, str) or not purpose:
            problems.append(f"{where}.for should say what the extension does")
            purpose = ""
        stand_in = str(row.get("stand_in") or "")
        if kind == "stand-in":
            path = Path(stand_in)
            if not stand_in or path.is_absolute() or ".." in path.parts or not (folder / path).is_dir():
                problems.append(f"{where}.stand_in should name a folder inside {folder.name}/")
        if kind == "optional" and not row.get("why"):
            problems.append(f"{where} is optional, so it needs a why: what the nodes do without it")
        if kind == "required" and not row.get("needs"):
            problems.append(f"{where} is required, so it needs a needs: what building it takes")
        out.append(
            Extension(
                row["package"],
                kind,
                purpose,
                str(row.get("why") or ""),
                str(row.get("needs") or ""),
                stand_in,
            )
        )
    return tuple(out)


def _sources(rows: Any, problems: list[str]) -> tuple[Source, ...]:
    if rows is None:
        return ()
    if not isinstance(rows, list):
        problems.append("sources should be a list")
        return ()
    out: list[Source] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            problems.append(f"sources[{i}] should be an object")
            continue
        name = row.get("name")
        if not isinstance(name, str) or not _SEGMENT.match(name) or name in (".", ".."):
            problems.append(f"sources[{i}].name should be one folder name")
            continue
        where = f"sources.{name}"
        if any(s.name == name for s in out):
            problems.append(f"{where} is listed twice")
            continue
        url = row.get("url")
        if not isinstance(url, str) or not url.startswith(("https://", "http://")):
            problems.append(f"{where}.url should be an http(s) address")
            continue
        sha = row.get("sha256")
        if not isinstance(sha, str) or not _SHA256.match(sha):
            problems.append(f"{where}.sha256 should be 64 lower-case hex digits")
            continue
        paths = row.get("paths") or ["."]
        if not isinstance(paths, list) or any(
            not isinstance(p, str) or Path(p).is_absolute() or ".." in Path(p).parts for p in paths
        ):
            problems.append(f"{where}.paths should be folders inside the archive")
            continue
        out.append(Source(name, url, sha, tuple(paths)))
    return tuple(out)


def _check_pyproject(folder: Path, builds: tuple[Build, ...], problems: list[str]) -> None:
    try:
        data = tomllib.loads((folder / PYPROJECT).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"{PYPROJECT}: cannot read: {exc}")
        return
    uv = (data.get("tool") or {}).get("uv") or {}
    if uv.get("package") is not False:
        problems.append(f"{PYPROJECT}: [tool.uv] package = false is missing; a runtime is not a package")
    extras = set(((data.get("project") or {}).get("optional-dependencies") or {}).keys())
    names = [b.name for b in builds]
    missing = [n for n in names if n not in extras]
    if missing:
        problems.append(f"{PYPROJECT}: no extra for the build(s) {', '.join(missing)}")
    if len(names) > 1:
        sets = [
            {str(item.get("extra")) for item in group if isinstance(item, dict)}
            for group in uv.get("conflicts") or []
        ]
        if not any(set(names) <= s for s in sets):
            problems.append(
                f"{PYPROJECT}: the builds {', '.join(names)} should be one set in [tool.uv] conflicts"
            )


def _check_lock(folder: Path, problems: list[str]) -> None:
    try:
        lock = tomllib.loads((folder / LOCK).read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        problems.append(f"{LOCK}: cannot read: {exc}")
        return
    for package in lock.get("package") or []:
        if package.get("name") != "torch":
            continue
        version = str(package.get("version", ""))
        try:
            too_old = version_tuple(version)[:2] < TORCH_FLOOR
        except ValueError:
            too_old = True
        if too_old:
            floor = ".".join(map(str, TORCH_FLOOR))
            problems.append(
                f"{LOCK} holds torch {version}; every runtime needs torch {floor} or newer "
                "(older torch can run code from a crafted weights file, CVE-2025-32434)"
            )


def parse(data: dict[str, Any], folder: Path, source: str = DEFINITION) -> RuntimeDef:
    """A runtime's definition, with every problem in it reported at once."""
    problems: list[str] = []
    runtime_id = _text(data, "id", problems)
    if runtime_id and not _ID.match(runtime_id):
        problems.append(f"id {runtime_id!r} should be lower case words joined by . _ or -")
    elif runtime_id and runtime_id != folder.name:
        problems.append(f"id {runtime_id!r} should match its folder, {folder.name}")
    title = _text(data, "title", problems)
    summary = _text(data, "summary", problems, required=False)
    python = _text(data, "python", problems)
    if python and not _PYTHON.match(python):
        problems.append(f"python {python!r} should be a CPython version like 3.12")
    builds = _builds(data.get("builds"), problems)
    extensions = _extensions(data.get("extensions"), folder, problems)
    sources = _sources(data.get("sources"), problems)
    env = data.get("env") or {}
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
    ):
        problems.append("env should map names to strings")
        env = {}
    probe = data.get("probe")
    if probe is not None:
        file, _, function = str(probe).partition(":")
        path = Path(file)
        if not function or path.is_absolute() or ".." in path.parts or not (folder / path).is_file():
            problems.append(f"probe should read file.py:function, with the file in {folder.name}/")
    for name in (PYPROJECT, LOCK):
        if not (folder / name).is_file():
            problems.append(f"{name} is missing")
    if (folder / PYPROJECT).is_file() and builds:
        _check_pyproject(folder, builds, problems)
    if (folder / LOCK).is_file():
        _check_lock(folder, problems)
    if problems:
        raise DefinitionError(source, problems)
    return RuntimeDef(
        id=runtime_id,
        title=title,
        python=python,
        builds=builds,
        folder=folder,
        summary=summary,
        extensions=extensions,
        sources=sources,
        env=dict(env),
        probe=str(probe) if probe else None,
        raw=data,
    )


def load(path: Path) -> RuntimeDef:
    """Read `<folder>/runtime.json` and check it with the files beside it."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DefinitionError(str(path), [f"cannot read: {exc}"]) from exc
    if not isinstance(data, dict):
        raise DefinitionError(str(path), ["the file should hold one JSON object"])
    return parse(data, path.parent, source=str(path))


@dataclass
class RuntimeSet:
    runtimes: dict[str, RuntimeDef] = field(default_factory=dict)
    problems: dict[str, list[str]] = field(default_factory=dict)


def discover(roots: Iterable[Path] | None = None) -> RuntimeSet:
    """Every runtime under the roots (default: the repo's runtimes/), found by looking."""
    found = RuntimeSet()
    for root in roots if roots is not None else [RUNTIMES_DIR]:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{DEFINITION}")):
            try:
                runtime = load(path)
            except DefinitionError as exc:
                found.problems[str(path)] = exc.problems
                continue
            if runtime.id in found.runtimes:
                other = found.runtimes[runtime.id].folder
                found.problems[str(path)] = [f"id {runtime.id!r} is already used by {other}"]
                continue
            found.runtimes[runtime.id] = runtime
    return found
