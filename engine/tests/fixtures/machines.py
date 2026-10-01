"""The machines spec 002's tests fit nodes on, as `hardware.profile()` dicts.

They are written from the spec's list of machine profiles and the plan's test table, not recorded
on real cards: they cover the range the app targets, from no GPU to an 80 GB card, so no test of a
hardware decision rests on one card. MB are 10^6 bytes, as the profile reports them; a card's total
is its MiB as nvidia-smi reports them (a 4096 MiB card is 4295 MB).

Card names are for people only. `renamed()` gives every card another name, and a fit that changes
with it is deciding on a name."""

from __future__ import annotations

import copy
from typing import Any

DRIVER = "580.88"


def card(index: int, capability: str, total_mb: int, free_mb: int) -> dict[str, Any]:
    return {
        "index": index,
        "vendor": "nvidia",
        "name": f"Test card {total_mb} MB, compute {capability}",
        "capability": capability,
        "vram_total_mb": total_mb,
        "vram_free_mb": free_mb,
    }


def machine(cards: list[dict[str, Any]], system_free_mb: int, system_total_mb: int) -> dict[str, Any]:
    count = len(cards)
    return {
        "os": "linux",
        "gpus": cards,
        "driver": DRIVER if cards else None,
        "nvidia": {
            "found": bool(cards),
            "why": f"nvidia-smi listed {count} card{'s' if count > 1 else ''}."
            if cards
            else "nvidia-smi was not found, so no NVIDIA card is known.",
        },
        "system": {"total_mb": system_total_mb, "free_mb": system_free_mb, "why": "A test machine."},
        "disk_free_mb": 10**6,
        "raw": "",
    }


CARD_8GB_HELD = card(0, "6.1", 8590, 7000)  # 1.5 GB held by other programs
CARD_24GB = card(0, "8.9", 25770, 24500)

MACHINES: dict[str, dict[str, Any]] = {
    "no GPU, 32 GB": machine([], 24000, 34360),
    "4 GB, compute 5.2, 16 GB": machine([card(0, "5.2", 4295, 3900)], 10000, 17180),
    "4 GB, compute 7.5, 16 GB": machine([card(0, "7.5", 4295, 3900)], 10000, 17180),
    "4 GB, compute 7.5, 8 GB": machine([card(0, "7.5", 4295, 3900)], 5000, 8590),
    "6 GB, compute 7.5": machine([card(0, "7.5", 6442, 6000)], 10000, 17180),
    "8 GB, compute 6.1, 1.5 GB held": machine([CARD_8GB_HELD], 10000, 17180),
    "12 GB, compute 8.6": machine([card(0, "8.6", 12885, 12000)], 24000, 34360),
    "24 GB, compute 8.9": machine([CARD_24GB], 50000, 68719),
    "80 GB, compute 9.0": machine([card(0, "9.0", 85899, 84000)], 200000, 274878),
    "two cards, 8 GB and 24 GB": machine([CARD_8GB_HELD, {**CARD_24GB, "index": 1}], 50000, 68719),
    "2 GB, compute 6.1, 4 GB": machine([card(0, "6.1", 2147, 1900)], 3000, 4295),
}


def get(name: str) -> dict[str, Any]:
    """A copy of a machine, which a test may change."""
    return copy.deepcopy(MACHINES[name])


def with_system(profile: dict[str, Any], free_mb: int, total_mb: int) -> dict[str, Any]:
    """The same machine with other system memory."""
    changed = copy.deepcopy(profile)
    changed["system"] = {"total_mb": total_mb, "free_mb": free_mb, "why": "A test machine."}
    return changed


def renamed(profile: dict[str, Any]) -> dict[str, Any]:
    """The same machine with every card given a name nothing could recognise."""
    changed = copy.deepcopy(profile)
    for gpu in changed["gpus"]:
        gpu["name"] = f"Renamed {gpu['index']} zz-9000"
    return changed
