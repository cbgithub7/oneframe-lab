"""The plan: which build of a runtime this machine gets, or why none can run.

Pure: it reads the machine's profile and the definition, nothing else, so planning starts no
process and opens no connection. No card's name is read: only its capability, memory and driver.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from oneframe.reading import VERSION, at_least, at_most
from oneframe.runtimes.definition import Build, RuntimeDef


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


def planned_card(profile: dict[str, Any]) -> dict[str, Any] | None:
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
    if driver and not VERSION.match(str(driver)):
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
    card = planned_card(profile)
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
