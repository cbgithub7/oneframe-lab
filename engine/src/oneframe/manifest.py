"""Node manifests: what a node is, what it takes and gives, and where it runs.

A node is a folder with a `node.json` and the code it names. Adding a model is adding a folder;
nothing in the engine or the app lists nodes by name. A manifest that is wrong is refused with
every problem named at once, so a person writing one fixes it in one pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe import ports

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
_ID = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_PORT_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


class ManifestError(ValueError):
    def __init__(self, source: str, problems: list[str]):
        self.source = source
        self.problems = problems
        super().__init__(f"{source}: " + "; ".join(problems))


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
    licence: dict[str, Any] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def code_path(self) -> Path:
        return self.folder / self.run.file

    def to_json(self) -> dict[str, Any]:
        """What the app is sent: the manifest as written, plus where it came from."""
        return dict(self.raw, folder=str(self.folder))


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
    choices = tuple(str(c) for c in row.get("choices") or ())
    if kind == "choice" and not choices:
        problems.append(f"{where} is a choice with no choices")
    default = row.get("default")
    if kind == "choice" and default is not None and default not in choices:
        problems.append(f"{where}.default {default!r} is not one of its choices")
    lo, hi = row.get("min"), row.get("max")
    if kind in ("int", "float") and default is not None:
        if not isinstance(default, int | float) or isinstance(default, bool):
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


def parse(data: dict[str, Any], folder: Path, source: str = "node.json") -> Manifest:
    problems: list[str] = []
    known = ports.types()

    def text(key: str, required: bool = True) -> str:
        value = data.get(key)
        if value is None and not required:
            return ""
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key} should be a non-empty string")
            return ""
        return value

    node_id = text("id")
    if node_id and not _ID.match(node_id):
        problems.append(f"id {node_id!r} should be lower case words joined by . _ or -")
    category = text("category")
    if category and category not in CATEGORIES:
        problems.append(f"category {category!r} is not one of {', '.join(CATEGORIES)}")

    def port_map(key: str) -> dict[str, ports.PortSpec]:
        rows = data.get(key) or {}
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

    inputs = port_map("inputs")
    outputs = port_map("outputs")
    if not outputs:
        problems.append("a node needs at least one output")
    for name, spec in outputs.items():
        if spec.optional:
            problems.append(f"outputs.{name}: outputs cannot be optional")

    params: dict[str, Param] = {}
    raw_params = data.get("params") or {}
    if not isinstance(raw_params, dict):
        problems.append("params should be an object")
    else:
        for name, row in raw_params.items():
            if name in inputs:
                problems.append(f"params.{name} has the same name as an input")
            parsed = _param(name, row, problems)
            if parsed:
                params[name] = parsed

    run_row = data.get("run") or {}
    where = run_row.get("where", "engine")
    entry = str(run_row.get("entry", "node.py:run"))
    file, _, function = entry.partition(":")
    if where not in WHERE:
        problems.append(f"run.where must be one of {', '.join(WHERE)}")
    if not function:
        problems.append("run.entry should read file.py:function")
    if Path(file).is_absolute() or ".." in Path(file).parts:
        problems.append("run.entry must name a file inside the node's folder")
    runtime = run_row.get("runtime")
    if where == "runtime" and not runtime:
        problems.append("run.runtime is required when run.where is runtime")
    if not (folder / file).is_file():
        problems.append(f"run.entry names {file}, which is not in {folder.name}/")

    devices = tuple(data.get("devices") or ("cpu",))
    for device in devices:
        if device not in ("cpu", "cuda", "mps", "xpu", "rocm"):
            problems.append(f"devices: unknown device {device!r}")

    if problems:
        raise ManifestError(source, problems)
    return Manifest(
        id=node_id,
        version=text("version"),
        title=text("title"),
        category=category,
        summary=text("summary", required=False),
        inputs=inputs,
        outputs=outputs,
        params=params,
        run=Run(where=where, file=file, function=function, runtime=runtime),
        folder=folder,
        devices=devices,
        licence=dict(data.get("licence") or {}),
        links=dict(data.get("links") or {}),
        raw=data,
    )


def load(path: Path) -> Manifest:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(str(path), [f"cannot read: {exc}"]) from exc
    if not isinstance(data, dict):
        raise ManifestError(str(path), ["the file should hold one JSON object"])
    return parse(data, path.parent, source=str(path))
