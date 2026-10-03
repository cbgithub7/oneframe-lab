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
import os
import re
import shutil
import threading
import tomllib
import traceback
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe import RUNTIMES_DIR, hardware, runtime_install
from oneframe.errors import Failure, Stopped, failure
from oneframe.executors import reserved_env
from oneframe.files import FileLock, lock_file
from oneframe.journal import journalled
from oneframe.layout import Layout
from oneframe.memory import Target

DEFINITION = "runtime.json"
PYPROJECT = "pyproject.toml"
LOCK = "uv.lock"
VENDORS = ("nvidia", "amd", "intel", "none")
OSES = ("windows", "linux", "macos")
EXTENSION_CLASSES = ("stand-in", "optional", "required")
# torch.load on a crafted file could run code before 2.6, weights_only or not (CVE-2025-32434), and
# its weights_only unpickler could corrupt memory, and possibly run code, before 2.10 (CVE-2026-24747).
TORCH_FLOOR = (2, 10)

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
        """What an installed environment was built from: the lock, the definition, and the
        stand-ins copied into it. Read now, not when the definition was loaded, so a change under a
        running engine makes the runtime out of date at once."""
        stand_ins = hashlib.sha256()
        for ext in self.extensions:
            if ext.kind != "stand-in":
                continue
            root = self.folder / ext.stand_in
            for path in sorted(p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts):
                stand_ins.update(path.relative_to(self.folder).as_posix().encode("utf-8") + b"\0")
                stand_ins.update(text_sha256(path).encode("ascii"))
        return {
            "lock_sha256": text_sha256(self.lock_path),
            "definition_sha256": text_sha256(self.definition_path),
            "stand_ins_sha256": stand_ins.hexdigest(),
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
                "(older torch can run code from a crafted weights file, even with weights_only: "
                "CVE-2025-32434, CVE-2026-24747)"
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
    reserved = sorted(name for name in env if reserved_env(name))
    if reserved:
        problems.append(
            f"env sets {', '.join(reserved)}, which the engine sets or removes for every run "
            "(hub libraries offline, no hub token or endpoint, torch's weights_only loading on)"
        )
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


_LOADED: dict[Path, tuple[tuple[Any, ...], RuntimeDef]] = {}


def _stamp(folder: Path) -> tuple[Any, ...]:
    """What changes when any file a definition is read from changes."""
    out: list[Any] = []
    for path in sorted(folder.rglob("*")) if folder.is_dir() else []:
        if path.is_file():
            stat = path.stat()
            out.append((str(path), stat.st_size, stat.st_mtime_ns))
    return tuple(out)


def load(path: Path) -> RuntimeDef:
    """Read `<folder>/runtime.json` and check it with the files beside it. A definition whose
    files have not changed since it was last read is not read again."""
    stamp = _stamp(path.parent)
    cached = _LOADED.get(path)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    runtime = _load(path)
    _LOADED[path] = (stamp, runtime)
    return runtime


def _load(path: Path) -> RuntimeDef:
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


# -- the plan: which build this machine gets ---------------------------------------------------


@dataclass
class Plan:
    runtime: str
    build: str | None  # None when blocked
    why: str
    blocked: str | None = None
    considered: list[dict[str, Any]] = field(default_factory=list)  # {build, chosen, why}, in order
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict[str, Any]:
        return {
            "runtime": self.runtime,
            "build": self.build,
            "why": self.why,
            "blocked": self.blocked,
            "considered": self.considered,
            "notes": self.notes,
        }


def at_least(value: str, floor: str) -> bool:
    a, b = version_tuple(value), version_tuple(floor)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) >= b + (0,) * (width - len(b))


def at_most(value: str, ceiling: str) -> bool:
    """`ceiling` "12" covers every 12.x; "12.0" covers 12.0 only."""
    top = version_tuple(ceiling)
    return version_tuple(value)[: len(top)] <= top


def _card(profile: dict[str, Any]) -> dict[str, Any] | None:
    """The card a plan is made for: the NVIDIA card with the most memory, the lowest index on a
    tie. A node runs on this card; choosing among several per node is a known limit (spec 002)."""
    cards = [g for g in profile.get("gpus") or [] if g.get("vendor") == "nvidia"]
    if not cards:
        return None
    return min(cards, key=lambda g: (-(g.get("vram_total_mb") or 0), g.get("index", 0)))


def _refusal(build: Build, card: dict[str, Any] | None, profile: dict[str, Any]) -> str | None:
    """Why this GPU build cannot run here, or None when it can. The driver is checked before the
    capability, so a machine held back by its driver is told that first."""
    os_name = str(profile.get("os") or "")
    if build.vendor != "nvidia":
        return f"needs an {build.vendor.upper()} card; only NVIDIA cards are read so far"
    if card is None:
        return "needs an NVIDIA card, and none was found"
    if build.os and os_name not in build.os:
        return f"runs on {', '.join(build.os)} only"
    if isinstance(build.min_driver, dict) and os_name not in build.min_driver:
        return f"is not offered on {os_name}"
    floor = build.driver_floor(os_name)
    driver = profile.get("driver")
    if driver and not _VERSION.match(str(driver)):
        driver = None  # nvidia-smi said something that is not a version: treat it as unknown
    if floor and not driver:
        return f"needs NVIDIA driver {floor} or newer, and the driver version is unknown"
    if floor and driver and not at_least(driver, floor):
        return f"needs NVIDIA driver {floor} or newer; this machine has {driver}"
    capability = card.get("capability")
    if (build.min_capability or build.max_capability) and not capability:
        return "needs a known compute capability, and this card's driver is too old to report it"
    if build.min_capability and capability and not at_least(capability, build.min_capability):
        return f"needs compute capability {build.min_capability} or higher; this card is {capability}"
    if build.max_capability and capability and not at_most(capability, build.max_capability):
        top = build.max_capability + (".x" if "." not in build.max_capability else "")
        return f"covers compute capability up to {top}; this card is {capability}"
    return None


def plan(runtime: RuntimeDef, profile: dict[str, Any], installed: Collection[str] = ()) -> Plan:
    """The build this machine should use, or why it cannot run the runtime at all.

    Pure: it reads the profile and the definition, nothing else. The fastest build the machine
    supports is chosen; then the processor build, if there is one; otherwise the runtime is
    blocked, with every build's reason. `installed` are the builds already on disk, which need
    no more disk space. No card's name is read here: only its capability, memory and driver."""
    card = _card(profile)
    result = Plan(runtime=runtime.id, build=None, why="")
    nvidia = profile.get("nvidia") or {}
    if card is None and nvidia.get("why"):
        result.notes.append(str(nvidia["why"]))
    gpus = [g for g in profile.get("gpus") or [] if g.get("vendor") == "nvidia"]
    if card is not None and len(gpus) > 1:
        result.notes.append(
            f"Planned for card {card.get('index')} of {len(gpus)}, the one with the most memory."
        )

    chosen: Build | None = None
    for build in runtime.builds:
        if build.vendor == "none":
            continue
        why = _refusal(build, card, profile)
        if why is None and chosen is None:
            chosen = build
            capability = card.get("capability") if card else None
            result.why = (
                f"{build.name} is the fastest build this machine runs "
                f"(compute capability {capability}, driver {profile.get('driver')})."
            )
            result.considered.append({"build": build.name, "chosen": True, "why": result.why})
        elif why is None:
            result.considered.append(
                {"build": build.name, "chosen": False, "why": "runs here, but is slower"}
            )
        else:
            result.considered.append({"build": build.name, "chosen": False, "why": f"{build.name} {why}"})
    processor = next((b for b in runtime.builds if b.vendor == "none"), None)
    os_name = str(profile.get("os") or "")
    if processor is not None and processor.os and os_name not in processor.os:
        result.considered.append(
            {
                "build": processor.name,
                "chosen": False,
                "why": f"{processor.name} runs on {', '.join(processor.os)} only",
            }
        )
        processor = None
    if processor is not None:
        if chosen is None:
            chosen = processor
            result.why = f"{processor.name} runs on the processor; no GPU build runs on this machine."
            result.considered.append({"build": processor.name, "chosen": True, "why": result.why})
        else:
            result.considered.append(
                {"build": processor.name, "chosen": False, "why": "a GPU build runs here"}
            )
    if chosen is None:
        reasons = "; ".join(row["why"] for row in result.considered)
        result.blocked = result.why = f"No build of {runtime.title} runs on this machine: {reasons}."
        return result

    for ext in runtime.extensions:
        if ext.kind == "optional":
            result.notes.append(f"{ext.package} ({ext.purpose}) is left out: {ext.why}")
        elif ext.kind == "stand-in":
            result.notes.append(f"{ext.package} ({ext.purpose}) is replaced by a stand-in.")
    required = [e for e in runtime.extensions if e.kind == "required"]
    blocker = ""
    if required:
        blocker = " ".join(
            f"{runtime.title} needs {e.package} ({e.purpose}), which has to be compiled: "
            f"that takes {e.needs}."
            for e in required
        )
        blocker += " Prebuilt wheels are not supported yet."
    free = profile.get("disk_free_mb")
    if (
        not blocker
        and chosen.name not in installed
        and chosen.disk_mb
        and free is not None
        and free < chosen.disk_mb
    ):
        blocker = f"{chosen.name} needs about {chosen.disk_mb} MB, and the data root has {free} MB free."
    if blocker:
        for row in result.considered:
            if row["chosen"]:
                row["chosen"] = False
                row["why"] = f"{chosen.name} fits this machine, but it cannot be installed."
        result.blocked = result.why = blocker
        return result
    result.build = chosen.name
    return result


# -- the manager: status, install, remove, and what the scheduler asks --------------------------


class RuntimeMissing(Failure, RuntimeError):
    """A node's runtime cannot run yet: kind `runtime`, with a reason declared in errors.py
    (not_installed, out_of_date, installing, blocked, unknown, moved, newer_format)."""

    kind = "runtime"

    def __init__(self, message: str, reason: str = "not_installed", next: str | None = None):
        super().__init__(message, reason=reason, next=next)


class InstallRefused(Failure, ValueError):
    """An install or remove that cannot start: kind `runtime`, with a reason declared in
    errors.py (locked when another process is installing or removing it, busy, blocked, ...)."""

    kind = "runtime"

    def __init__(self, message: str, reason: str, next: str | None = None):
        super().__init__(message, reason=reason, next=next)


def find_uv(explicit: str | None = None, env: dict[str, str] | None = None) -> str | None:
    """uv: as given, else the UV variable `uv run` sets for the engine, else ONEFRAME_UV, else PATH."""
    source = os.environ if env is None else env
    return explicit or source.get("UV") or source.get("ONEFRAME_UV") or shutil.which("uv")


Emit = Callable[[dict[str, Any]], None]


class Runtimes:
    """Every runtime this engine knows, and its state on this machine.

    Definitions are read again on every call, so a lock changed under a running engine counts at
    once. The machine profile is read on first use and when asked to refresh. Listing and planning
    start no process but nvidia-smi and open no connection; only install fetches anything."""

    def __init__(
        self,
        data: Path,
        roots: list[Path] | None = None,
        uv: str | None = None,
        profile: Callable[[], dict[str, Any]] | None = None,
        uv_home: Path | None = None,
    ):
        # Absolute, because a runtime's child runs in a folder of its own: a relative path to the
        # probe or the interpreter would point somewhere else from there.
        self.data = Path(data).resolve()
        self.roots = [Path(r).resolve() for r in roots] if roots is not None else [RUNTIMES_DIR]
        self.uv = uv
        self.uv_home = uv_home
        self._read_profile = profile or (lambda: hardware.profile(data))
        self._profile: dict[str, Any] | None = None
        self._lock = threading.Lock()
        self._busy: tuple[str, threading.Event] | None = None
        self._held: FileLock | None = None  # the busy runtime's lock, across processes

    # reading

    def definitions(self) -> RuntimeSet:
        return discover(self.roots)

    def profile(self, refresh: bool = False) -> dict[str, Any]:
        if self._profile is None or refresh:
            self._profile = self._read_profile()
        return self._profile

    def get(self, runtime_id: str) -> RuntimeDef:
        found = self.definitions()
        if runtime_id not in found.runtimes:
            problems = next(
                (p for path, p in found.problems.items() if Path(path).parent.name == runtime_id), None
            )
            detail = f": {'; '.join(problems)}" if problems else ""
            raise RuntimeMissing(f"No runtime called {runtime_id!r} is defined{detail}.", reason="unknown")
        return found.runtimes[runtime_id]

    def _markers(self, runtime: RuntimeDef) -> dict[str, dict[str, Any]]:
        out = {}
        for build in runtime.builds:
            marker = runtime_install.read_marker(runtime_install.env_dir(self.data, runtime.id, build.name))
            if marker is not None:
                out[build.name] = marker
        return out

    def plan_for(self, runtime: RuntimeDef, refresh: bool = False) -> Plan:
        return plan(runtime, self.profile(refresh), installed=set(self._markers(runtime)))

    def installing(self) -> str | None:
        busy = self._busy
        return busy[0] if busy else None

    def status(self, runtime: RuntimeDef, the_plan: Plan | None = None) -> dict[str, Any]:
        """installing, installed, out_of_date, not_installed, newer_format (its marker is from a
        newer app) or moved (built in another root), for the build the plan picks."""
        the_plan = the_plan or self.plan_for(runtime)
        markers = self._markers(runtime)
        build = the_plan.build
        on_disk = [
            b.name for b in runtime.builds if runtime_install.env_dir(self.data, runtime.id, b.name).is_dir()
        ]
        marker = markers.get(build) if build else None
        problem = (
            runtime_install.marker_problem(runtime_install.env_dir(self.data, runtime.id, str(build)), marker)
            if marker is not None
            else None
        )
        hashes = runtime.hashes()
        if self.installing() == runtime.id:
            state = "installing"
        elif marker is None:
            state = "not_installed"
        elif problem is not None:
            state = problem
        elif {k: marker.get(k) for k in hashes} != hashes:
            state = "out_of_date"
        elif not runtime_install.interpreter(
            runtime_install.env_dir(self.data, runtime.id, str(build))
        ).exists():
            state = "not_installed"
        else:
            state = "installed"
        return {
            "status": state,
            "build": build or next(iter(markers), None),
            "size_bytes": marker.get("size_bytes") if marker else None,
            "on_disk": on_disk,
            "installed": {
                name: {"python": m.get("python"), "installed_at": m.get("installed_at")}
                for name, m in markers.items()
            },
        }

    def list(self) -> dict[str, Any]:
        found = self.definitions()
        rows = []
        for runtime in found.runtimes.values():
            the_plan = self.plan_for(runtime)
            rows.append(
                {
                    "id": runtime.id,
                    "title": runtime.title,
                    "summary": runtime.summary,
                    "python": runtime.python,
                    **self.status(runtime, the_plan),
                    "plan": the_plan.to_json(),
                }
            )
        return {"runtimes": rows, "problems": found.problems, "profile": self.profile_summary()}

    def profile_summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.profile().items() if k != "raw"}

    def plans(self, runtime_id: str | None = None, refresh: bool = False) -> dict[str, Any]:
        self.profile(refresh)
        found = self.definitions()
        chosen = [self.get(runtime_id)] if runtime_id else list(found.runtimes.values())
        return {"plans": [self.plan_for(r).to_json() for r in chosen], "profile": self.profile_summary()}

    # what the scheduler asks

    def python_for(self, runtime_id: str) -> Path:
        """The interpreter of the build this machine runs, or RuntimeMissing saying why not."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        if the_plan.blocked:
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} cannot run here: {the_plan.blocked}", reason="blocked"
            )
        state = self.status(runtime, the_plan)["status"]
        install = f"Install the runtime {runtime_id}."
        if state == "installing":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is being installed; run again when it is done.",
                "installing",
                "Run again when the install is done.",
            )
        if state == "newer_format":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} was installed by a newer version of the app, and is left "
                "as it is. Use that version, or another data root.",
                reason="newer_format",
            )
        if state == "moved":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} was built in another data root and moved here; install "
                "it again to rebuild it.",
                reason="moved",
                next=install,
            )
        if state == "out_of_date":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is out of date: its lock or definition changed since it "
                "was installed. Install it again to rebuild it.",
                reason="out_of_date",
                next=install,
            )
        if state != "installed":
            raise RuntimeMissing(
                f"The runtime {runtime_id!r} is not installed; install its {the_plan.build} build first.",
                reason="not_installed",
                next=install,
            )
        return runtime_install.interpreter(
            runtime_install.env_dir(self.data, runtime.id, str(the_plan.build))
        )

    def env_for(self, runtime_id: str) -> dict[str, str]:
        """runtime.json's variables. On a machine with several NVIDIA cards, also the card the plan
        was made for, so that a node's "cuda" is that card: CUDA numbers cards fastest first unless
        told to use the PCI order nvidia-smi reports them in."""
        runtime = self.get(runtime_id)
        env = dict(runtime.env)
        profile = self.profile()
        cards = [g for g in profile.get("gpus") or [] if g.get("vendor") == "nvidia"]
        the_plan = self.plan_for(runtime)
        build = runtime.build(str(the_plan.build)) if the_plan.build else None
        card = _card(profile)
        if len(cards) > 1 and card is not None and build is not None and build.vendor == "nvidia":
            env.setdefault("CUDA_DEVICE_ORDER", "PCI_BUS_ID")
            env.setdefault("CUDA_VISIBLE_DEVICES", str(card.get("index", 0)))
        return env

    def key_for(self, runtime_id: str) -> dict[str, str]:
        """What a node's cache key takes from its runtime: the build this machine runs, and the
        hashes of the lock, definition and stand-ins that build was installed from."""
        runtime = self.get(runtime_id)
        build = str(self.plan_for(runtime).build)
        marker = self._markers(runtime).get(build) or {}
        return {"build": build, **{k: str(marker.get(k, "")) for k in runtime.hashes()}}

    def caches_for(self, runtime_id: str) -> Path:
        """Where a node in this runtime keeps its libraries' caches: `cache/runtime/<id>/`."""
        return Layout(self.data).runtime_caches / runtime_id

    def device_target(self, runtime_id: str) -> Target:
        """What a node in this runtime can use, for the fit: the card the plan's build was made
        for (none for a processor build, or a machine without one), and the lock it was built from,
        which what a machine learns is kept under."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        build = runtime.build(str(the_plan.build)) if the_plan.build else None
        card = _card(self.profile())
        lock = f"{the_plan.build}:{runtime.hashes()['lock_sha256'][:16]}"
        if build is None or build.vendor != "nvidia" or card is None:
            return Target(card=None, lock=lock)
        return Target(card=int(card.get("index", 0)), lock=lock)

    # changing

    def _runtime_lock(self, runtime_id: str) -> FileLock:
        """Held across processes while a runtime is installed or removed; a second is refused."""
        lock = FileLock(lock_file(Layout(self.data).locks, f"runtime-{runtime_id}"))
        if not lock.acquire():
            raise InstallRefused(
                f"{runtime_id} is being installed or removed by another process on this data root; "
                "try again when it is done.",
                reason="locked",
                next="Try again when it is done.",
            )
        return lock

    def begin_install(
        self, runtime_id: str, build: str | None = None
    ) -> tuple[RuntimeDef, str, threading.Event] | None:
        """Check an install can start and claim the one install slot. None: already installed."""
        runtime = self.get(runtime_id)
        the_plan = self.plan_for(runtime)
        if the_plan.blocked:
            raise InstallRefused(the_plan.blocked, "blocked")
        planned = str(the_plan.build)
        if build is not None and build != planned:
            if runtime.build(build) is None:
                raise InstallRefused(
                    f"The runtime {runtime_id!r} has no build called {build!r}.", "wrong_build"
                )
            why = next((r["why"] for r in the_plan.considered if r["build"] == build), "")
            raise InstallRefused(
                f"This machine runs the {planned} build of {runtime_id!r}; "
                f"only the planned build is installed. {why}".strip(),
                "wrong_build",
            )
        if not uv_available(self.uv):
            raise InstallRefused("uv was not found.", "no_uv", "Install uv, or set ONEFRAME_UV to its path.")
        with self._lock:
            if self._busy is not None:
                raise InstallRefused(
                    f"{self._busy[0]} is being installed; runtimes are installed one at a time.",
                    "busy",
                    "Try again when it is done.",
                )
            held = self._runtime_lock(runtime_id)
            try:
                state = self.status(runtime, the_plan)["status"]
                if state == "newer_format":
                    raise InstallRefused(
                        f"The runtime {runtime_id!r} was installed by a newer version of the app; it "
                        "is left as it is.",
                        "newer_format",
                    )
            except BaseException:
                held.release()
                raise
            if state == "installed":
                held.release()
                return None
            stop = threading.Event()
            self._busy = (runtime_id, stop)
            self._held = held
        return runtime, planned, stop

    def run_install(
        self,
        runtime: RuntimeDef,
        build: str,
        stop: threading.Event,
        emit: Emit,
        opener: Callable[..., Any] | None = None,
    ) -> dict[str, Any] | None:
        """Carry out a claimed install, reporting it as runtime.* events. Returns the result, or
        None when it stopped or failed (the events say which)."""
        base = {"runtime": runtime.id, "build": build}
        extra: dict[str, Any] = {"opener": opener} if opener is not None else {}
        with journalled(emit, Layout(self.data).journals, f"install-{runtime.id}") as emit:
            try:
                emit({**base, "event": "runtime.start"})
                done = runtime_install.install(
                    runtime, build, self.data, str(self.uv), emit, stop.is_set, self.uv_home, **extra
                )
            except Stopped:
                emit(
                    {
                        **base,
                        "event": "runtime.stopped",
                        "message": "Install stopped. Installing again picks up where it was.",
                    }
                )
                return None
            except runtime_install.InstallFailed as exc:
                emit({**base, "event": "runtime.failed", **exc.to_json(detail=exc.detail)})
                return None
            except Exception as exc:
                trace = traceback.format_exc()[-4000:]
                crashed = failure("engine", f"{type(exc).__name__}: {exc}", detail=trace)
                emit({**base, "event": "runtime.failed", **crashed})
                raise
            finally:  # the slot and the lock go before runtime.done, so a next install may start at once
                with self._lock:
                    self._busy = None
                    held, self._held = self._held, None
                    if held is not None:
                        held.release()
            emit({**base, "event": "runtime.done", **done})
            return done

    def install(
        self, runtime_id: str, build: str | None = None, emit: Emit = lambda _e: None
    ) -> dict[str, Any] | None:
        """Install and wait: begin_install, then run_install on this thread."""
        claimed = self.begin_install(runtime_id, build)
        if claimed is None:
            return {"runtime": runtime_id, "already": True}
        return self.run_install(*claimed, emit=emit)

    def stop(self, runtime_id: str) -> bool:
        busy = self._busy
        if busy is None or busy[0] != runtime_id:
            return False
        busy[1].set()
        return True

    def remove(self, runtime_id: str) -> dict[str, Any]:
        """Delete `<data>/runtimes/<id>/`, and nothing else."""
        self.get(runtime_id)
        if self.installing() == runtime_id:
            raise InstallRefused(
                f"{runtime_id} is being installed; stop the install before removing it.", "installing"
            )
        with self._runtime_lock(runtime_id):
            return self._remove(runtime_id)

    def _remove(self, runtime_id: str) -> dict[str, Any]:
        root = (self.data / "runtimes").resolve()
        target = runtime_install.runtime_dir(self.data, runtime_id)
        if not target.exists():
            return {"runtime": runtime_id, "removed": None}
        for env in (p for p in target.iterdir() if p.is_dir()):
            marker = runtime_install.read_marker(env)
            if marker is not None and runtime_install.marker_problem(env, marker) == "newer_format":
                raise InstallRefused(
                    f"The {env.name} build of {runtime_id!r} was installed by a newer version of the "
                    "app; it is left as it is.",
                    "newer_format",
                )
        if target.is_symlink() or target.resolve().parent != root or target.name != runtime_id:
            raise InstallRefused(
                f"Refusing to delete {target}: it is not a runtime folder in {root}.", "unsafe_path"
            )
        runtime_install.remove_tree(target)
        return {"runtime": runtime_id, "removed": str(target)}


def uv_available(uv: str | None) -> bool:
    return bool(uv) and (Path(str(uv)).is_file() or shutil.which(str(uv)) is not None)
