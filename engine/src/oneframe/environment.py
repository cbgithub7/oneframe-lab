"""What a child process inherits from the engine, and what the engine sets in it.

Two kinds of child start from one inherited environment (`inherited`): uv, with the Pythons it
starts during an install, and the runtime child that runs a node. Neither gets the engine's own
Python (its interpreter, packages, bytecode prefix or folders on PATH) or a hub token. A run then
gets what keeps it offline and its writes under the data root (`child_env`); uv gets its cache and
its Pythons under the data root (`uv_environment`).

Names are compared upper-cased on every system: Windows treats HF_TOKEN and hf_token as one
variable, and one rule is simpler than two. Standard library only.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

TEMP_VARIABLES = ("TMP", "TEMP", "TMPDIR")
# The engine's own Python: inherited, each would point uv, a Python it starts, or a run at the
# engine's environment. PYTHONPYCACHEPREFIX is the engine's (the app sets it): an install would
# compile its bytecode there, and a run would look for its runtime's there and never find it.
# PYTHONDONTWRITEBYTECODE would stop an install compiling; a run sets it again.
ENGINE_PYTHON = (
    "PYTHONPATH",
    "PYTHONHOME",
    "VIRTUAL_ENV",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "PYTHONPYCACHEPREFIX",
    "UV_PROJECT_ENVIRONMENT",
    "UV_PYTHON",
    "PYTHONDONTWRITEBYTECODE",
)
# Never passed to any child: a run reads only files already on disk and uv fetches only from
# package indexes, so neither needs a hub token or endpoint, and nothing may turn torch's
# weights_only loading back off.
SECRETS = (
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HF_ENDPOINT",
    "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD",
)
# Set in every runtime child, after everything else, so neither the person's environment nor a
# runtime's definition can turn them off. Hub libraries told they are offline read only what is on
# disk and fail at once on anything missing, instead of retrying for seconds against a closed
# network. torch then loads every checkpoint with weights_only, whatever the caller asks for.
FORCED_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # A token file that holds nothing: with HF_TOKEN unset, the hub library would otherwise read the
    # person's own (~/.cache/huggingface/token) and send it with every request.
    "HF_TOKEN_PATH": os.devnull,
    "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
}
# Where the libraries a node uses keep their caches: each under the data root, in
# `cache/runtime/<id>/`, never in the person's home folder, where nothing would remove them.
LIBRARY_CACHES = {
    "HF_HOME": "huggingface",
    "TORCH_HOME": "torch",
    "TORCH_EXTENSIONS_DIR": "torch_extensions",
    "TORCHINDUCTOR_CACHE_DIR": "torchinductor",
    "TRITON_HOME": "triton",
    "CUDA_CACHE_PATH": "cuda",
    "XDG_CACHE_HOME": "xdg",
    "MPLCONFIGDIR": "matplotlib",
}
# Variables that point a library past the folders above: each overrides HF_HOME or TRITON_HOME,
# so the person's own value would let a run read weights Download never fetched, or write outside
# the root. They are removed; the folders above then decide (spec 003 sets HF_HUB_CACHE itself).
OVERRIDING_CACHES = (
    "HF_HUB_CACHE",
    "HUGGINGFACE_HUB_CACHE",
    "HF_ASSETS_CACHE",
    "HUGGINGFACE_ASSETS_CACHE",
    "HF_DATASETS_CACHE",
    "HF_MODULES_CACHE",
    "HF_XET_CACHE",
    "TRANSFORMERS_CACHE",
    "PYTORCH_TRANSFORMERS_CACHE",
    "PYTORCH_PRETRAINED_BERT_CACHE",
    "TRITON_CACHE_DIR",
)
# A child reads the bytecode its runtime's install compiled and writes none: not beside a node's
# code, and not into the runtime, which an install alone changes.
ROOT_ENV = (*LIBRARY_CACHES, *TEMP_VARIABLES, *OVERRIDING_CACHES, "PYTHONDONTWRITEBYTECODE")
# uv's cache and the Pythons it fetches, in folders under uv's own (`uv/` in the layout), and what
# keeps it from writing outside them. app/main/paths.js sets the same for the engine it starts.
UV_FOLDERS = {"UV_CACHE_DIR": "cache", "UV_PYTHON_INSTALL_DIR": "python"}
UV_SETTINGS = {"UV_PYTHON_INSTALL_BIN": "0", "UV_PYTHON_INSTALL_REGISTRY": "0"}


def _upper(names: Iterable[str]) -> set[str]:
    return {name.upper() for name in names}


_RESERVED = _upper((*FORCED_ENV, *SECRETS, *ROOT_ENV))
_ROOT = _upper(ROOT_ENV)


def reserved_why(names: list[str]) -> str:
    """Why the engine keeps these names to itself, in words for a runtime's author."""
    why = []
    if any(name.upper() not in _ROOT for name in names):
        why.append("hub libraries offline, no hub token or endpoint, torch's weights_only loading on")
    if any(name.upper() in _ROOT for name in names):
        why.append("a run's caches, temporary files and bytecode kept under the data root")
    return "; ".join(why)


def reserved_env(name: str) -> bool:
    """Whether the engine sets or removes this variable for every run."""
    return name.upper() in _RESERVED


def _put(env: dict[str, str], values: Mapping[str, str]) -> None:
    """Set each value, after removing any other spelling of its name."""
    names = _upper(values)
    for name in [k for k in env if k.upper() in names and k not in values]:
        del env[name]
    env.update(values)


def _inside(path: str, folder: Path) -> bool:
    try:
        return Path(os.path.normcase(str(Path(path).resolve()))).is_relative_to(folder)
    except OSError, ValueError:
        return False


def inherited(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """What every child starts from: `base` (this process's environment when None) without the
    engine's own Python, the engine's folders on PATH, or the secrets."""
    drop = _upper((*ENGINE_PYTHON, *SECRETS))
    env = {k: v for k, v in (os.environ if base is None else base).items() if k.upper() not in drop}
    engine = Path(os.path.normcase(str(Path(sys.prefix).resolve())))
    for name in [k for k in env if k.upper() == "PATH"]:
        parts = env[name].split(os.pathsep)
        env[name] = os.pathsep.join(part for part in parts if part and not _inside(part, engine))
    return env


def child_env(
    base: Mapping[str, str] | None,
    extra: Mapping[str, str],
    caches: Path | None = None,
    tmp: Path | None = None,
) -> dict[str, str]:
    """A runtime child's environment: the inherited one, less every name the engine reserves, then
    the runtime's and the job's own (`extra`, less the reserved names), then FORCED_ENV. Then what
    keeps a run's writes under the data root: the library caches in `caches` (the runtime's folder
    in `cache/runtime/`), the temporary folder in `tmp` (the job's own), and no bytecode written."""
    env = {k: v for k, v in inherited(base).items() if not reserved_env(k)}
    env.update({k: v for k, v in extra.items() if not reserved_env(k)})
    env.update(FORCED_ENV)
    if caches is not None:
        env.update({name: str(caches / folder) for name, folder in LIBRARY_CACHES.items()})
    if tmp is not None:
        env.update(dict.fromkeys(TEMP_VARIABLES, str(tmp)))
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    _put(env, {"PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
    return env


def uv_environment(
    uv_home: Path, env: Path | None = None, base: Mapping[str, str] | None = None
) -> dict[str, str]:
    """uv's cache and Pythons under the data root, where removing the data root removes them, and
    where the cache and the environments share a volume, so uv links files instead of copying.
    uv writes nothing outside it: no `python3.x` link in the person's bin folder, and no entry in
    the Windows registry, either of which would outlive the data root and point at nothing. `env`
    is the runtime environment uv builds, when it builds one."""
    out = inherited(base)
    values = {name: str(uv_home / folder) for name, folder in UV_FOLDERS.items()}
    values.update(UV_SETTINGS, PYTHONIOENCODING="utf-8")
    if env is not None:
        values["UV_PROJECT_ENVIRONMENT"] = str(env)
    _put(out, values)
    return out
