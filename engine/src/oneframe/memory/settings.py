"""The person's control over the fit, from `<data>/settings.json`, and the margin it leaves free
on each device."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oneframe.files import format_of
from oneframe.layout import Layout
from oneframe.reading import number

MARGIN_FLOOR_MB = 1611  # 1.5 GiB, in MB of 10^6 bytes
MARGIN_SHARE = 0.10  # of the device's total, when that is larger
SETTINGS_FILE = "settings.json"
SETTINGS_FORMAT = 1  # a file that does not say is format 1
FIT_MODES = ("on", "off")


@dataclass(frozen=True)
class Settings:
    """The person's control over the fit, from `<data>/settings.json`'s `memory` object."""

    margin_mb: Mapping[str, float | None] = field(default_factory=lambda: {"cuda": None, "cpu": None})
    never_reduce_quality: bool = False
    fit: str = "on"
    notes: tuple[str, ...] = ()  # what in the file was not understood, and so left at its default

    def to_json(self) -> dict[str, Any]:
        return {
            "margin_mb": dict(self.margin_mb),
            "never_reduce_quality": self.never_reduce_quality,
            "fit": self.fit,
            **({"notes": list(self.notes)} if self.notes else {}),
        }


def read_settings(data_root: Path | None) -> Settings:
    """The settings, read again at each fit. A missing file is the defaults; a value that is not
    understood is left at its default, and said so in `notes`. A file in a newer format is read as
    the defaults, with a note, and left as it is."""
    if data_root is None:
        return Settings()
    path = Layout(Path(data_root)).settings
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Settings()
    except (OSError, ValueError) as exc:
        return Settings(notes=(f"{SETTINGS_FILE} could not be read ({exc}); the defaults are used",))
    found = format_of(data, missing=SETTINGS_FORMAT)
    if found != SETTINGS_FORMAT:
        why = (
            f"is in format {found}, newer than this version of the app knows ({SETTINGS_FORMAT})"
            if found is not None and found > SETTINGS_FORMAT
            else "does not say a format this version of the app knows"
        )
        return Settings(notes=(f"{SETTINGS_FILE} {why}; the defaults are used",))
    row = data.get("memory") if isinstance(data, dict) else None
    if row is None:
        return Settings()
    if not isinstance(row, dict):
        return Settings(notes=(f"{SETTINGS_FILE}: memory should be an object; the defaults are used",))
    notes: list[str] = []
    margins: dict[str, float | None] = {"cuda": None, "cpu": None}
    for device, value in (row.get("margin_mb") or {}).items():
        if device not in margins:
            notes.append(f"margin_mb.{device}: only cuda and cpu have a margin")
        elif value is not None and (not number(value) or value < 0):
            notes.append(f"margin_mb.{device} should be MB, or null for the default")
        else:
            margins[device] = None if value is None else float(value)
    never = row.get("never_reduce_quality", False)
    if not isinstance(never, bool):
        notes.append("never_reduce_quality should be true or false")
        never = False
    mode = row.get("fit", "on")
    if mode not in FIT_MODES:
        notes.append('fit should be "on" or "off"')
        mode = "on"
    return Settings(margins, never, mode, tuple(notes))


def margin(settings: Settings, device_type: str, total_mb: float | None) -> float:
    """The person's margin for this type of device, or the larger of 1.5 GiB and 10% of its total."""
    chosen = settings.margin_mb.get(device_type)
    if chosen is not None:
        return float(chosen)
    return max(MARGIN_FLOOR_MB, MARGIN_SHARE * total_mb) if total_mb else float(MARGIN_FLOOR_MB)
