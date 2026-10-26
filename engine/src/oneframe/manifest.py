"""Node manifests: what a node is, what it takes and gives, and where it runs.

A node is a folder with a `node.json` and the code it names. Adding a model is adding a folder;
nothing in the engine or the app lists nodes by name. A manifest that is wrong is refused with
every problem named at once, so a person writing one fixes it in one pass.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple

from oneframe import ports
from oneframe.memory.model import PRECISION_PARAM, MemoryModel
from oneframe.memory.model import parse as parse_memory
from oneframe.models.schema import FileSpec
from oneframe.models.schema import parse as parse_files
from oneframe.reading import ProblemsError, check_id, number, optional_text, read_object, text

CATEGORIES = (
    "source",  # brings something in: a photo, clicks, text
    "depth",  # one image to depth, points, camera, normals
    "segment",  # masks
    "object",  # one image (or cut-out) to a mesh or splat of one object
    "scene",  # composes or places assets
    "views",  # synthesises new views
    "reconstruct",  # views to cameras, points, splats or meshes
    "render",  # an asset to images along a camera path
    "repair",  # fixes, remeshes, simplifies
    "texture",  # repaints an asset
    "convert",  # changes a value's facets or form without a model
    "evaluate",  # measures results
    "export",  # writes a file for another program
    "test",  # only in the test suite
)

PARAM_TYPES = ("int", "float", "bool", "string", "choice", "file")
WHERE = ("engine", "runtime")
_PORT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ManifestError(ProblemsError):
    """A `node.json` that cannot be read, or holds problems."""


@dataclass(frozen=True)
class Param:
    name: str
    type: str
    default: Any = None
    label: str = ""
    doc: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    # "output" when changing it changes the result (part of the cache key); "speed" when it only
    # changes how the result is reached (batch size, offloading) and the output stays the same.
    affects: str = "output"


@dataclass(frozen=True)
class Run:
    where: str
    file: str
    function: str
    runtime: str | None = None
    load: str | None = None  # `file.py:function`, called once per attempt before `run`


@dataclass(frozen=True)
class Manifest:
    id: str
    version: str
    title: str
    category: str
    summary: str
    inputs: dict[str, ports.PortSpec]
    outputs: dict[str, ports.PortSpec]
    params: dict[str, Param]
    run: Run
    folder: Path
    devices: tuple[str, ...] = ("cpu",)
    memory: MemoryModel | None = None
    licence: dict[str, Any] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    # the files it reads (spec 003), and the choice param that picks a checkpoint's files
    files: tuple[FileSpec, ...] = ()
    files_by: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)
    # The sha256 of the bytes this manifest was parsed from: the cache key's node.json part, so a
    # result is kept under the manifest a run used, not one edited on disk since (spec 003).
    digest: str = ""

    @property
    def code_path(self) -> Path:
        return self.folder / self.run.file

    @property
    def default_checkpoint(self) -> str | None:
        """The checkpoint a run reads unless the graph or the fit picks another; None without one."""
        return None if self.files_by is None else str(self.params[self.files_by].default)  # never None

    def files_for(self, checkpoint: str | None) -> tuple[FileSpec, ...]:
        """The files a run at `checkpoint` reads: every shared file, and that checkpoint's own."""
        return tuple(f for f in self.files if f.wanted_by(checkpoint))

    def to_json(self) -> dict[str, Any]:
        """What the app is sent: the manifest as written, plus where it came from and the params
        the engine added (`precision`, from a memory model)."""
        out = dict(self.raw, folder=str(self.folder))
        if self.memory is not None:
            out["params"] = {**(self.raw.get("params") or {}), PRECISION_PARAM: self.memory.precision_param()}
        return out


def check_param(key: str, param: Param, value: Any) -> str | None:
    """Why `value` is not a valid value of `param`, or None. A graph's values and a memory model's
    changes are held to the same rules."""
    kind = param.type
    if kind == "bool" and not isinstance(value, bool):
        return f"{key} should be true or false"
    if kind in ("int", "float"):
        if not number(value):
            return f"{key} should be a number"
        if kind == "int" and not float(value).is_integer():
            return f"{key} should be a whole number"
        if param.minimum is not None and value < param.minimum:
            return f"{key} is below its minimum {param.minimum}"
        if param.maximum is not None and value > param.maximum:
            return f"{key} is above its maximum {param.maximum}"
    if kind == "choice" and value not in param.choices:
        return f"{key} should be one of {', '.join(param.choices)}"
    if kind in ("string", "file") and not isinstance(value, str):
        return f"{key} should be text"
    if kind == "file" and isinstance(value, str) and value and not Path(value).is_file():
        return f"{key}: no file at {value}"
    return None


def _param(name: str, row: Any, problems: list[str]) -> Param | None:
    where = f"params.{name}"
    if not isinstance(row, dict):
        problems.append(f"{where} should be an object")
        return None
    kind = row.get("type")
    if kind not in PARAM_TYPES:
        problems.append(f"{where}.type must be one of {', '.join(PARAM_TYPES)}")
        return None
    affects = row.get("affects", "output")
    if affects not in ("output", "speed"):
        problems.append(f"{where}.affects must be output or speed")
    listed = row.get("choices")
    if listed is not None and not isinstance(listed, list):
        problems.append(f"{where}.choices should be a list")
        listed = None
    choices = tuple(str(c) for c in listed or ())
    if kind == "choice" and not choices:
        problems.append(f"{where} is a choice with no choices")
    default = row.get("default")
    if kind == "choice" and default is not None and default not in choices:
        problems.append(f"{where}.default {default!r} is not one of its choices")
    lo, hi = _bound(row, "min", where, problems), _bound(row, "max", where, problems)
    if kind in ("int", "float") and default is not None:
        if not number(default):
            problems.append(f"{where}.default should be a number")
        elif (lo is not None and default < lo) or (hi is not None and default > hi):
            problems.append(f"{where}.default {default} is outside [{lo}, {hi}]")
    return Param(
        name=name,
        type=kind,
        default=default,
        label=str(row.get("label", "")),
        doc=str(row.get("doc", "")),
        minimum=lo,
        maximum=hi,
        choices=choices,
        affects=affects,
    )


def _bound(row: dict[str, Any], key: str, where: str, problems: list[str]) -> float | None:
    """A param's `min` or `max`: a number, or None when not given (or not a number, a problem)."""
    value = row.get(key)
    if value is None or number(value):
        return value
    problems.append(f"{where}.{key} should be a number")
    return None


def _code(field: str, value: str, folder: Path, problems: list[str]) -> tuple[str, str]:
    """A `file.py:function` in the node's folder, as `run.entry` and `run.load` name one."""
    file, _, function = value.partition(":")
    if not function:
        problems.append(f"{field} should read file.py:function")
    if Path(file).is_absolute() or ".." in Path(file).parts:
        problems.append(f"{field} must name a file inside the node's folder")
    if not (folder / file).is_file():
        problems.append(f"{field} names {file}, which is not in {folder.name}/")
    return file, function


class _Head(NamedTuple):
    id: str
    version: str
    title: str
    category: str
    summary: str


def _head(data: dict[str, Any], problems: list[str]) -> _Head:
    """`id`, `version`, `title`, `category` and `summary`, which may be blank."""
    node_id = check_id(text(data, "id", problems), problems)
    version = text(data, "version", problems)
    title = text(data, "title", problems)
    category = text(data, "category", problems)
    if category and category not in CATEGORIES:
        problems.append(f"category {category!r} is not one of {', '.join(CATEGORIES)}")
    summary = optional_text(data, "summary", problems, default="") or ""
    return _Head(node_id, version, title, category, summary)


def _port_map(
    data: dict[str, Any], key: str, known: dict[str, ports.PortType], problems: list[str]
) -> dict[str, ports.PortSpec]:
    rows = data.get(key)
    if rows is None:
        return {}
    if not isinstance(rows, dict):
        problems.append(f"{key} should be an object of port name to spec")
        return {}
    out: dict[str, ports.PortSpec] = {}
    for name, spec in rows.items():
        if not _PORT_NAME.match(name):
            problems.append(f"{key}.{name}: port names are lower_snake_case")
            continue
        try:
            out[name] = ports.parse(spec, known)
        except ports.PortError as exc:
            problems.append(f"{key}.{name}: {exc}")
    return out


def _ports(
    data: dict[str, Any], problems: list[str]
) -> tuple[dict[str, ports.PortSpec], dict[str, ports.PortSpec]]:
    """`inputs` and `outputs`: at least one output, and none of them optional."""
    known = ports.types()
    inputs = _port_map(data, "inputs", known, problems)
    outputs = _port_map(data, "outputs", known, problems)
    if not outputs:
        problems.append("a node needs at least one output")
    for name, spec in outputs.items():
        if spec.optional:
            problems.append(f"outputs.{name}: outputs cannot be optional")
    return inputs, outputs


def _params(data: dict[str, Any], inputs: dict[str, ports.PortSpec], problems: list[str]) -> dict[str, Param]:
    """`params`, none named as an input is, and none the engine adds."""
    params: dict[str, Param] = {}
    rows = data.get("params")
    if rows is None:
        return params
    if not isinstance(rows, dict):
        problems.append("params should be an object")
        return params
    for name, row in rows.items():
        if name in inputs:
            problems.append(f"params.{name} has the same name as an input")
        if name == PRECISION_PARAM:
            problems.append("params.precision is added by the engine from a memory model; remove it")
            continue
        parsed = _param(name, row, problems)
        if parsed:
            params[name] = parsed
    return params


def _run(data: dict[str, Any], folder: Path, problems: list[str]) -> Run | None:
    """`run`: where the node runs, the code it runs and loads, and the runtime it runs in; None
    when it is not an object."""
    row = data.get("run")
    if row is None:
        row = {}
    if not isinstance(row, dict):
        problems.append("run should be an object")
        return None
    where = row.get("where", "engine")
    if where not in WHERE:
        problems.append(f"run.where must be one of {', '.join(WHERE)}")
    entry = optional_text(row, "entry", problems, where="run", default="node.py:run")
    file, function = ("", "") if entry is None else _code("run.entry", entry, folder, problems)
    load = optional_text(row, "load", problems, where="run")
    if load is not None:
        _code("run.load", load, folder, problems)
    runtime = optional_text(row, "runtime", problems, where="run")
    if where == "runtime" and not runtime:
        problems.append("run.runtime is required when run.where is runtime")
    return Run(where=where, file=file, function=function, runtime=runtime, load=load)


def _devices(data: dict[str, Any], problems: list[str]) -> tuple[str, ...] | None:
    """`devices`, in the order the node prefers them; the processor alone when none are given;
    None when it is not a list."""
    rows = data.get("devices")
    if rows is None:
        rows = []
    if not isinstance(rows, list):
        problems.append('devices should be a list, such as ["cuda", "cpu"]')
        return None
    for device in rows:
        if device not in ("cpu", "cuda", "mps", "xpu", "rocm"):
            problems.append(f"devices: unknown device {device!r}")
    return tuple(rows) or ("cpu",)


def _object(data: dict[str, Any], key: str, problems: list[str]) -> dict[str, Any]:
    """`licence` or `links`: an object, shown as it is written."""
    rows = data.get(key)
    if rows is None:
        return {}
    if not isinstance(rows, dict):
        problems.append(f"{key} should be an object")
        return {}
    return dict(rows)


def _memory_and_files(
    data: dict[str, Any],
    params: dict[str, Param],
    inputs: dict[str, ports.PortSpec],
    devices: tuple[str, ...],
    where: str,
    problems: list[str],
) -> tuple[MemoryModel | None, tuple[FileSpec, ...], str | None]:
    """`memory`, which adds its `precision` param to `params`, and `files` with the param that
    picks a checkpoint's."""
    model = None
    if data.get("memory") is not None:
        model = parse_memory(data["memory"], params, inputs, devices, problems, check_param)
        precision = model and _param(PRECISION_PARAM, model.precision_param(), problems)
        if precision:
            params[PRECISION_PARAM] = precision
    weights_by = data["memory"].get("weights_by") if isinstance(data.get("memory"), dict) else None
    files, files_by = parse_files(data, params, where, weights_by, problems)
    return model, files, files_by


def parse(
    data: dict[str, Any], folder: Path, source: str = "node.json", read: bytes | None = None
) -> Manifest:
    """A node's manifest, with every problem in it reported at once: each part adds its own, then
    one error names them all.

    `read`: the bytes `data` was read from, whose sha256 is the manifest's digest; without them
    (a manifest made in code) the digest is of `data` written out in one canonical form."""
    problems: list[str] = []
    head = _head(data, problems)
    inputs, outputs = _ports(data, problems)
    params = _params(data, inputs, problems)
    run = _run(data, folder, problems)
    devices = _devices(data, problems)
    licence = _object(data, "licence", problems)
    links = _object(data, "links", problems)
    model: MemoryModel | None = None
    files: tuple[FileSpec, ...] = ()
    files_by: str | None = None
    # The memory model and the files are checked against where and on what the node runs: when
    # either cannot be read, their problems would only mislead.
    if run is not None and devices is not None:
        model, files, files_by = _memory_and_files(data, params, inputs, devices, run.where, problems)
    if problems or run is None or devices is None:
        raise ManifestError(source, problems)
    return Manifest(
        id=head.id,
        version=head.version,
        title=head.title,
        category=head.category,
        summary=head.summary,
        inputs=inputs,
        outputs=outputs,
        params=params,
        run=run,
        folder=folder,
        devices=devices,
        memory=model,
        licence=licence,
        links=links,
        files=files,
        files_by=files_by,
        raw=data,
        digest=hashlib.sha256(
            read if read is not None else json.dumps(data, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest(),
    )


def load(path: Path) -> Manifest:
    data, read = read_object(path, ManifestError)
    return parse(data, path.parent, source=str(path), read=read)
