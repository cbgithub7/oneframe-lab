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

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

ROOT_FILE = "oneframe-root.json"
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
