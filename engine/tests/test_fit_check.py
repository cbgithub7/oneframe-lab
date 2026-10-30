"""Spec 002's check on a card (`npm run bench:fit-check`), on the processor: how it sizes its runs
from the node's own model on any card, and what its report says. What it measures on a card is a
local session's, on real hardware."""

from __future__ import annotations

from typing import Any

import fit_check
import pytest

from oneframe.registry import discover


def test_the_holder_leaves_room_for_the_first_change_only_on_every_card() -> None:
    plan = fit_check.find_ac8()
    manifest = discover([plan.folder.parent]).get(plan.node)
    for total, free in ((6442, 6000), (8590, 7000), (12885, 11000), (25770, 24500), (85899, 84000)):
        card = {"index": 0, "capability": "8.6", "vram_total_mb": total, "vram_free_mb": free}
        margin = max(1611, 0.1 * total)
        for context in (None, 94.0, 300.0):
            sized = fit_check.holder_size(manifest, card, margin, context)
            assert sized is not None
            hold, (low, high) = sized
            assert low < high
            if hold <= 0:
                continue  # the bench says the card has too little free memory
            # The holder's own context comes on top of what it holds.
            budget = free - hold - (context or 0.0) - margin
            assert low < budget < high
            assert budget == pytest.approx((low + high) / 2)


def test_the_report_says_when_a_step_did_not_happen_as_designed() -> None:
    def row(device: str, steps_oom: int, ooms: int, free: float | None) -> dict[str, Any]:
        refusal = None if free is None else {"request_mb": 2287.0, "card_free_mb": free, "allowed_mb": 2824.0}
        return {
            "status": "done",
            "seconds": 4.7,
            "steps_oom": [{}] * steps_oom,
            "ooms": [{}] * ooms,
            "fits": [{"attempt": 1, "device": device, "changes": []}],
            "exercised": device == "cuda" and bool(steps_oom or ooms),
            "refusal": refusal,
            "by_cap": None if refusal is None else free > 2287.0,  # type: ignore[operator]
        }

    record: dict[str, Any] = {
        "mode": "ac8",
        "node": "test.vram",
        "date": "2026-10-01T00:00:00+00:00",
        "engine": "0",
        "card": {"index": 0, "name": "Some card", "capability": "6.1", "vram_total_mb": 8590},
        "context": {"context_mb": 94.0},
        "settings": [],
        "holder": {
            **row("cpu", 0, 0, None),
            "label": "defaults, with memory held",
            "held_mb": 3982,
            "budget_mb": 2309,
            "window_mb": [2377, 2761],
            "expected_changes": [0],
            "shown": False,
        },
        "overrun": {
            "budget_mb": 2825,
            "estimate_mb": 2761,
            "fallbacks": row("cpu", 0, 0, None),
            "retry": row("cuda", 0, 1, 2000.0),
        },
    }
    report = fit_check.render(record)
    assert "- Shown: **no** (the fit did not make the expected change" in report
    assert "budget then: 2,309 MB; the node needs 2,377 MB after its first change" in report
    assert report.count("**not exercised**") == 1
    assert report.count("**not shown**: torch's message did not give") == 1
    assert "2,000 MB free on the card and 2,824 MB allowed: **the card had no room for it" in report

    record["holder"]["shown"] = True
    record["overrun"]["fallbacks"] = row("cuda", 1, 0, 6550.0)
    record["overrun"]["retry"] = row("cuda", 0, 1, 6550.0)
    report = fit_check.render(record)
    assert "- Shown: yes" in report and "did not make the expected change" not in report
    assert "not exercised" not in report and "not shown" not in report and "no room" not in report
    assert report.count("torch refused 2,287 MB with 6,550 MB free on the card") == 2
    assert report.count("the cap refused it, so the driver was never asked") == 2


def test_ac8_is_described_by_its_node_folder_and_sized_from_the_nodes_own_model() -> None:
    plan = fit_check.find_ac8()
    assert plan.node == "test.vram" and (plan.folder / "node.json").is_file()
    manifest = discover([plan.folder.parent]).get(plan.node)
    for total, free in ((4295, 3900), (8590, 7000), (25770, 24500), (85899, 84000)):
        card = {"index": 0, "capability": "8.6", "vram_total_mb": total, "vram_free_mb": free}
        budget = free - max(1611, 0.1 * total)
        settings = fit_check.three_settings(manifest, plan, budget, card)
        values = [s[plan.scale] for s in settings]
        assert values == sorted(values) and all(v % 64 == 0 for v in values)
        for share, params in zip(plan.shares, settings, strict=True):
            need = fit_check._need(manifest.memory, {**fit_check._defaults(manifest), **params}, card)  # type: ignore[arg-type]
            assert need is not None
            assert need <= share * budget or params[plan.scale] == manifest.params[plan.scale].minimum
    small = fit_check.three_settings(manifest, plan, 3000, {"vram_total_mb": 4295})
    large = fit_check.three_settings(manifest, plan, 70000, {"vram_total_mb": 85899})
    assert large[-1][plan.scale] > small[-1][plan.scale]


def test_the_report_says_what_was_not_measured() -> None:
    record: dict[str, Any] = {
        "mode": "ac8",
        "node": "test.vram",
        "date": "2026-10-01T00:00:00+00:00",
        "engine": "0",
        "card": {"index": 0, "name": "Some card", "capability": "8.6", "vram_total_mb": 12885},
        "profile": {"driver": "580.88"},
        "context": {"why": "nvidia-smi was not found"},
        "settings": [],
        "holder": {"why": "The card's free memory could not be read."},
        "overrun": {
            "budget_mb": 3000,
            "estimate_mb": 2936,
            "fallbacks": {"status": "done", "seconds": 2.5, "steps_oom": [{}], "ooms": [], "fits": []},
            "retry": {"status": "done", "seconds": 4.0, "steps_oom": [], "ooms": [{}], "fits": []},
        },
    }
    report = fit_check.render(record)
    assert "**Context: not measured**" in report and "Not measured: nvidia-smi was not found" in report
    assert "Not measured: The card's free memory could not be read." in report
    assert report.count("**not shown**") == 2  # the overruns' events carry no message here
    assert " 0 MB" not in report  # a number not measured is never written as zero
    assert fit_check.render({"node": "test.vram", "why": "no card"}).count("Not measured: no card") == 1


# torch's message from attempt 1 of the engine's retry in the GTX 1070 report of 2026-10-01
# (specs/002-fit-to-memory/reports/2026-10-01-gtx-1070-fit-rerun.md), shortened to two processes.
CAPPED = (
    "OutOfMemoryError: CUDA out of memory. Tried to allocate 2.13 GiB. GPU 0 has a total capacity of "
    "8.00 GiB of which 6.10 GiB is free. Process 9132 has 17179869184.00 GiB memory in use. Including "
    "non-PyTorch memory, this process has 17179869184.00 GiB memory in use. 2.63 GiB allowed; Of the "
    "allocated memory 954.00 MiB is allocated by PyTorch, and 0 bytes is reserved by PyTorch but "
    "unallocated."
)


def test_torchs_message_shows_whether_the_cap_refused_a_request_the_card_had_room_for() -> None:
    gib = 1024**3 / 1e6
    assert fit_check.cap_refusal(CAPPED) == {
        "request_mb": round(2.13 * gib, 1),
        "card_free_mb": round(6.10 * gib, 1),
        "allowed_mb": round(2.63 * gib, 1),
    }
    full = CAPPED.replace("of which 6.10 GiB is free", "of which 512.00 MiB is free")
    assert fit_check.cap_refusal(full)["card_free_mb"] == round(512 * 1024**2 / 1e6, 1)  # type: ignore[index]
    # Without a cap, or not torch's message at all, it shows nothing.
    assert fit_check.cap_refusal(CAPPED.replace("2.63 GiB allowed; ", "")) is None
    assert fit_check.cap_refusal("CUDA out of memory") is None and fit_check.cap_refusal(None) is None
