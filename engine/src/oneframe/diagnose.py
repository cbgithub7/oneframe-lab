"""`npm run diagnose`: one file a person can hand over when something went wrong.

    npm run diagnose [-- --data <root>] [--out <file>]

It holds the versions, the machine profile, the runtimes and their state, the settings, what this
machine learned, and the latest journals and logs, and is written to the root's `reports/`. It holds
no environment variable. Before it is written, the user's own segment of every path under the
users folder is removed, in every spelling a path takes on its way into a log (backslashes, forward
slashes, JSON-escaped, any case, the 8.3 short name), so a user name that is also a word elsewhere
is kept where it is only a word; and Hugging Face tokens (`hf_...`) are scrubbed.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import subprocess
import sys
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from oneframe import __version__, hardware
from oneframe.layout import Layout, RootRefused, claim_root, default_root
from oneframe.runtimes import Runtimes, find_uv

REPORT_FORMAT = 1
JOURNALS = 5  # the latest, each to its last JOURNAL_LINES lines
JOURNAL_LINES = 2000
STEP_LOGS = 10
LOG_LINES = 400
USER = "<user>"

# One or more separators as they appear in text: \, /, or a JSON-escaped \\.
_SEP = r"(?:\\\\|\\|/)+"
_TOKEN = re.compile(r"hf_[A-Za-z0-9]{16,}")
_BOUNDARY_AFTER = r"(?![\w.~-])"
_BOUNDARY_BEFORE = r"(?<![\w.~-])"


def _home_pattern(home: str) -> re.Pattern[str] | None:
    parts = [p for p in re.split(r"[\\/]+", home) if p]
    if not parts:
        return None
    *users, user = parts
    drive = ""
    if users and re.fullmatch(r"[A-Za-z]:", users[0]):
        drive = f"(?:{re.escape(users.pop(0))})?"
    prefix = _BOUNDARY_BEFORE + drive + "".join(_SEP + re.escape(u) for u in users) + _SEP
    return re.compile(f"({prefix}){re.escape(user)}{_BOUNDARY_AFTER}", re.IGNORECASE)


def redact(text: str, homes: Iterable[str]) -> str:
    """`text` with the user's segment of each home path (and of its other spellings) replaced by
    <user>, and Hugging Face tokens scrubbed."""
    for home in sorted(set(homes), key=len, reverse=True):
        pattern = _home_pattern(home)
        if pattern is not None:
            text = pattern.sub(lambda m: m.group(1) + USER, text)
    return _TOKEN.sub("hf_<redacted>", text)


def short_names(path: Path) -> list[str]:
    """The 8.3 short spelling of `path` on Windows, when it has one."""
    if sys.platform != "win32":
        return []
    import ctypes

    buffer = ctypes.create_unicode_buffer(32768)
    length = ctypes.windll.kernel32.GetShortPathNameW(str(path), buffer, len(buffer))
    short = buffer.value if 0 < length < len(buffer) else ""
    return [short] if short and short.lower() != str(path).lower() else []


def _tail(path: Path, lines: int) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not be read: {exc})"
    return "\n".join(text.splitlines()[-lines:])


def _latest(folder: Path, pattern: str, count: int) -> list[Path]:
    try:
        files = [p for p in folder.glob(pattern) if p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: (p.stat().st_mtime_ns, p.name), reverse=True)[:count]


def _json_file(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        return {"unreadable": str(exc), "text": _tail(path, 200)}


def _uv_version(uv: str | None) -> str | None:
    if not uv:
        return None
    try:
        done = subprocess.run([uv, "--version"], capture_output=True, text=True, timeout=30, check=False)
    except OSError as exc:
        return f"(could not be run: {exc})"
    return done.stdout.strip() or done.stderr.strip()


def report(data: Path, runtime_roots: list[Path] | None = None) -> dict[str, Any]:
    """Everything the file holds, before it is redacted."""
    layout = Layout(data)
    uv = find_uv()
    manager = Runtimes(data, runtime_roots, uv=uv)
    try:
        runtimes: Any = manager.list()
    except Exception as exc:  # a broken definition must not cost the rest of the report
        runtimes = {"failed": f"{type(exc).__name__}: {exc}"}
    return {
        "format": REPORT_FORMAT,
        "created": datetime.now(UTC).isoformat(timespec="seconds"),
        "versions": {
            "engine": __version__,
            "python": sys.version,
            "platform": platform.platform(),
            "uv": _uv_version(uv),
        },
        "root": str(data),
        "root_file": _json_file(layout.root_file),
        "machine": hardware.profile(data),
        "runtimes": runtimes,
        "settings": _json_file(layout.settings),
        "learned": _json_file(layout.learned),
        "journals": {p.name: _tail(p, JOURNAL_LINES) for p in _latest(layout.journals, "*.ndjson", JOURNALS)},
        "logs": {
            **{p.name: _tail(p, LOG_LINES) for p in _latest(layout.logs, "*.log", 3)},
            **{f"steps/{p.name}": _tail(p, LOG_LINES) for p in _latest(layout.step_logs, "*.log", STEP_LOGS)},
        },
    }


def write(data: Path, out: Path, homes: Iterable[str], runtime_roots: list[Path] | None = None) -> Path:
    text = redact(json.dumps(report(data, runtime_roots), indent=2, default=str, ensure_ascii=False), homes)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="diagnose", description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--data", type=Path, default=None, help="the data root (default: the dev root)")
    parser.add_argument("--out", type=Path, default=None, help="the file (default: <data>/reports/)")
    parser.add_argument("--runtimes", type=Path, action="append", default=None, help="a folder of runtimes")
    args = parser.parse_args(argv)
    data = args.data or default_root()
    data.mkdir(parents=True, exist_ok=True)
    try:
        claim_root(data, packaged=False, version=__version__)
    except RootRefused as exc:
        print(exc, file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or Layout(data).reports / f"diagnose-{stamp}.json"
    home = Path.home()
    written = write(data, out, [str(home), *short_names(home)], args.runtimes)
    print(f"The report is at {written}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
