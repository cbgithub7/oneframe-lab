"""Where things live: the data root, and every path under it.

Everything the engine, a runtime child, the app or a command-line tool writes goes under one data
root (AGENTS.md, "One data root"). This module is the one place the engine computes the root and
names what is under it; `app/main/paths.js` mirrors it, and `contracts/layout.json` holds the
cases both test suites check, so the two never disagree about where a file is.

The root is a pure function of the environment, the platform, the home folder and whether the app
is packaged, so a test can ask it about any machine. Only the app chooses the packaged root (from
`app.isPackaged`) and passes it to the engine with `--data`; the engine's own default, which the
command-line tools use, is the dev root, so a dev checkout and a packaged app never share one.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oneframe.files import format_of, write_json

ROOT_FILE = "oneframe-root.json"
# https://bford.info/cachedir/: backup tools that honour it skip the cache, which can be rebuilt.
CACHEDIR_TAG = (
    "Signature: 8a477f597d28d172789f06886806bc55\n"
    "# This file is a cache directory tag created by Oneframe Lab.\n"
    "# For information about cache directory tags, see https://bford.info/cachedir/\n"
)
# 0: a root from before spec 006, without a root file. 1: the root file, and the folders Layout names.
LAYOUT_FORMAT = 1
KINDS = ("dev", "packaged")
NAMES = {
    # platform family: (dev root, packaged root)
    "win32": ("OneframeLab-dev", "OneframeLab"),
    "other": ("oneframe-lab-dev", "oneframe-lab"),
}

_DRIVE = re.compile(r"^[A-Za-z]:\\")
_UNC = re.compile(r"^\\\\[^\\]+\\[^\\]+")


def absolute(value: str | None, platform: str) -> str | None:
    """`value` as an absolute path in the platform's spelling, with `.`, `..` and repeated or
    trailing separators gone; None when it is not absolute (empty, relative, or on Windows a path
    without a drive or share, such as `C:x` or `\\x`), since nothing here knows the working folder.
    Spelled out rather than left to ntpath or Node's path, which disagree at the edges."""
    if not value:
        return None
    if platform == "win32":
        sep = "\\"
        text = value.replace("/", sep)
        anchor = _DRIVE.match(text) or _UNC.match(text)
        if anchor is None:
            return None
        head, rest = anchor.group(0).rstrip(sep), text[anchor.end() :]
    else:
        sep = "/"
        if not value.startswith(sep):
            return None
        head, rest = "", value
    parts: list[str] = []
    for part in rest.split(sep):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return head + sep + sep.join(parts)


def _join(base: str, platform: str, *names: str) -> str:
    sep = "\\" if platform == "win32" else "/"
    return base.rstrip(sep) + sep + sep.join(names)


def data_root(env: Mapping[str, str], platform: str, home: str, packaged: bool) -> str:
    """The data root for this environment, platform and home folder.

    ONEFRAME_DATA wins when it is absolute. Otherwise, on Windows, a folder in LOCALAPPDATA (or
    `<home>\\AppData\\Local`); elsewhere, in XDG_DATA_HOME (or `<home>/.local/share`). A variable
    that is empty or relative is ignored, as the XDG spec says of its own."""
    chosen = absolute(env.get("ONEFRAME_DATA"), platform)
    if chosen is not None:
        return chosen
    family = "win32" if platform == "win32" else "other"
    name = NAMES[family][1 if packaged else 0]
    home_dir = absolute(home, platform)
    if home_dir is None:
        raise ValueError(f"the home folder is not an absolute path: {home!r}")
    if platform == "win32":
        base = absolute(env.get("LOCALAPPDATA"), platform) or _join(home_dir, platform, "AppData", "Local")
    else:
        base = absolute(env.get("XDG_DATA_HOME"), platform) or _join(home_dir, platform, ".local", "share")
    return _join(base, platform, name)


def default_root() -> Path:
    """The engine's own default: the dev root on this machine. The app passes `--data`."""
    return Path(data_root(os.environ, sys.platform, str(Path.home()), packaged=False))


def keep_bytecode_under(root: Path) -> None:
    """Write the bytecode of every module imported from now on (the nodes' own code among them)
    under the root's `cache/pycache/`, not beside its source. The app sets PYTHONPYCACHEPREFIX as
    well, so the engine's own modules, imported before this runs, are kept there too."""
    sys.pycache_prefix = str(Layout(root).pycache)


@dataclass(frozen=True)
class Layout:
    """Every path under one data root, by name. `contracts/layout.json` lists them as well, and a
    test holds the two equal, as it does `app/main/paths.js`'s `layout()`."""

    root: Path

    @property
    def root_file(self) -> Path:
        """The layout's format, and whether a dev checkout or a packaged app made the root."""
        return self.root / ROOT_FILE

    @property
    def settings(self) -> Path:
        return self.root / "settings.json"

    @property
    def learned(self) -> Path:
        """What this machine learned about each node's memory."""
        return self.root / "memory" / "learned.json"

    @property
    def cache(self) -> Path:
        """Node outputs by key; everything under it can be rebuilt."""
        return self.root / "cache"

    @property
    def cachedir_tag(self) -> Path:
        """Tells backup tools the cache need not be kept."""
        return self.root / "cache" / "CACHEDIR.TAG"

    @property
    def tmp(self) -> Path:
        """One folder per process: its temporary files, run and job folders, bench scratch."""
        return self.root / "cache" / "tmp"

    @property
    def runtime_caches(self) -> Path:
        """`<id>/` per runtime: the caches model libraries keep (HF_HOME, TORCH_HOME, ...)."""
        return self.root / "cache" / "runtime"

    @property
    def pycache(self) -> Path:
        """The engine's own bytecode, kept out of the install folder and nodes/."""
        return self.root / "cache" / "pycache"

    @property
    def electron_cache(self) -> Path:
        """Electron's session data: its HTTP and GPU caches."""
        return self.root / "cache" / "electron"

    @property
    def app_tmp(self) -> Path:
        """The app's temporary folder: Electron's, and that of the uv that starts the engine. Not in
        `tmp`, whose unlocked folders an engine's start removes."""
        return self.root / "cache" / "electron" / "tmp"

    @property
    def electron(self) -> Path:
        """Electron's user data."""
        return self.root / "electron"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def runtimes(self) -> Path:
        """`<id>/<build>/` per installed runtime environment."""
        return self.root / "runtimes"

    @property
    def uv_cache(self) -> Path:
        return self.root / "uv" / "cache"

    @property
    def uv_python(self) -> Path:
        """The Pythons uv fetches, kept here rather than in the person's own folders."""
        return self.root / "uv" / "python"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def crashes(self) -> Path:
        return self.root / "logs" / "crashes"

    @property
    def locks(self) -> Path:
        """Lock files, never deleted: the operating system releases a lock when its holder dies."""
        return self.root / "logs" / "locks"

    @property
    def reports(self) -> Path:
        """Bench reports and diagnostics, written for a person to read or hand over."""
        return self.root / "reports"

    @classmethod
    def names(cls) -> list[str]:
        return sorted(name for name, value in vars(cls).items() if isinstance(value, property))


class RootRefused(RuntimeError):
    """The engine cannot use this data root: its layout is newer than this engine knows
    (`newer_layout`), or its root file cannot be read (`unreadable`). Nothing under it changed."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def claim_root(root: Path, packaged: bool, version: str = "") -> list[str]:
    """Check the root's layout before anything is written under it, and write its root file when
    it has none (format 0) or an older one. Raises RootRefused for a newer layout. Returns the
    warnings a person should see: a root the other kind of app made is used, but said so."""
    path = Layout(root).root_file
    kind = KINDS[1 if packaged else 0]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = None
    except (OSError, ValueError) as exc:
        raise RootRefused(f"{path} cannot be read ({exc}); nothing was changed.", "unreadable") from exc
    found = 0 if data is None else format_of(data)
    if found is None:
        raise RootRefused(f"{path} does not say its format; nothing was changed.", "unreadable")
    if found > LAYOUT_FORMAT:
        raise RootRefused(
            f"The data root {root} was laid out by a newer version of Oneframe Lab (layout {found}; "
            f"this version knows {LAYOUT_FORMAT}). Use the newer version, or another data root.",
            "newer_layout",
        )
    tag = Layout(root).cachedir_tag
    if not tag.exists():
        tag.parent.mkdir(parents=True, exist_ok=True)
        tag.write_text(CACHEDIR_TAG, encoding="utf-8")
    warnings: list[str] = []
    made_by = data.get("kind") if isinstance(data, dict) else None
    if made_by in KINDS and made_by != kind:
        warnings.append(
            f"The data root {root} belongs to the {made_by} app; this {kind} app is using it, as "
            "ONEFRAME_DATA asked."
        )
    if found < LAYOUT_FORMAT:
        write_json(
            path,
            {
                "format": LAYOUT_FORMAT,
                "kind": made_by if made_by in KINDS else kind,
                "created": datetime.now(UTC).isoformat(timespec="seconds"),
                "engine": version,
            },
            indent=2,
        )
    return warnings
