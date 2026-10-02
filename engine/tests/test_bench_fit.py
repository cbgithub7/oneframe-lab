"""`bench:fit`'s plumbing, on the processor: a node run at given settings, each estimate beside the
peak it measured, and the report. What it measures on a card is AC8's, on real hardware."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import MakeNode

from oneframe import bench_fit
from oneframe.registry import discover

SOURCE = "allocated by the node itself"

# Small enough to fit in any machine's free memory: 40 MB of weights and 20 + resolution / 10 MB of
# working memory, allocated as the model says.
SMALL_NODE: dict[str, Any] = {
    "id": "test.bench",
    "version": "1",
    "title": "Bench",
    "category": "test",
    "outputs": {"text": "Text"},
    "params": {
        "resolution": {"type": "int", "default": 400, "min": 10, "max": 4000},
        "fast": {"type": "bool", "default": False, "affects": "speed"},
    },
    "devices": ["cpu"],
    "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
    "memory": {
        "precisions": {"fp32": {"cuda_min_capability": None, "cpu": True}},
        "weights": {"fp32": 40, "source": SOURCE},
        "working": {"mb": 20, "terms": [{"coef": 0.1, "of": ["resolution"]}], "source": SOURCE},
    },
}
CODE = """
def run(ctx):
    mb = 40 + 20 + 0.1 * ctx.params["resolution"]
    block = bytearray(int(mb * 1_000_000))
    for i in range(0, len(block), 4096):
        block[i] = 1
    p = ctx.path("t.txt"); p.write_text("x"); ctx.output("text", p)
"""


def test_a_node_is_measured_at_each_group_of_settings(
    tmp_path: Path, make_node: MakeNode, node_root: Path
) -> None:
    make_node(SMALL_NODE, CODE)
    registry = discover([node_root])
    scheduler = bench_fit.make_scheduler(
        registry, None, tmp_path / "cache", runtime_python=lambda _runtime: Path(sys.executable)
    )
    groups = [bench_fit.parse_sets(["resolution=400"], registry.get("test.bench")), {"resolution": 800}, {}]
    record = bench_fit.bench_node(scheduler, "test.bench", groups)
    rows = record["runs"]
    assert [r["status"] for r in rows] == ["done", "done", "done"], [r["error"] for r in rows]
    assert [r["estimate_mb"] for r in rows] == [100, 140, 100]
    assert [r["device"] for r in rows] == ["cpu", "cpu", "cpu"]
    assert all(r["free_before_mb"] for r in rows)
    if sys.platform in ("linux", "win32"):  # resident memory is read there
        for row in rows:
            assert row["measured_mb"] is not None and row["measured_mb"] >= 0.8 * row["estimate_mb"]
            assert row["ratio"] == pytest.approx(row["measured_mb"] / row["estimate_mb"], abs=0.001)
    # The same settings twice still run: each run has a cache of its own.
    assert all(not r["error"] for r in rows) and rows[0]["fits"] and rows[2]["fits"]
    report = bench_fit.render({**record, "date": "2026-10-01T00:00:00+00:00", "engine": "0"})
    assert "| resolution=400 | cpu |" in report and "| defaults | cpu |" in report


def test_the_bench_learns_nothing_so_every_estimate_is_the_nodes_own(
    tmp_path: Path, make_node: MakeNode, node_root: Path
) -> None:
    # A store without a data root still learns in memory; on a card, an overrun's peak
    # once corrected the overrun's estimate off the card (spec 002's first AC8 run).
    # Here the first run allocates 200 MB its model does not know of, as an overrun does.
    spills = {**SMALL_NODE["params"], "extra": {"type": "int", "default": 0, "min": 0, "max": 1000}}
    make_node(
        {**SMALL_NODE, "params": spills},
        CODE.replace('ctx.params["resolution"]', 'ctx.params["resolution"] + ctx.params["extra"]'),
    )
    registry = discover([node_root])
    scheduler = bench_fit.make_scheduler(
        registry, None, tmp_path / "cache", runtime_python=lambda _runtime: Path(sys.executable)
    )
    groups = [{"resolution": 800, "extra": 200}, {"resolution": 800}, {"resolution": 800}]
    rows = bench_fit.bench_node(scheduler, "test.bench", groups)["runs"]
    assert [r["status"] for r in rows] == ["done", "done", "done"], [r["error"] for r in rows]
    assert [r["estimate_mb"] for r in rows] == [140, 140, 140]
    assert [f["estimate"] for r in rows for f in r["fits"]] == ["known", "known", "known"]


def test_the_holder_leaves_room_for_the_first_change_only_on_every_card() -> None:
    plan = bench_fit.find_ac8()
    manifest = discover([plan.folder.parent]).get(plan.node)
    for total, free in ((6442, 6000), (8590, 7000), (12885, 11000), (25770, 24500), (85899, 84000)):
        card = {"index": 0, "capability": "8.6", "vram_total_mb": total, "vram_free_mb": free}
        margin = max(1611, 0.1 * total)
        for context in (None, 94.0, 300.0):
            sized = bench_fit.holder_size(manifest, card, margin, context)
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
    report = bench_fit.render(record)
    assert "- Shown: **no** (the fit did not make the expected change" in report
    assert "budget then: 2,309 MB; the node needs 2,377 MB after its first change" in report
    assert report.count("**not exercised**") == 1
    assert report.count("**not shown**: torch's message did not give") == 1
    assert "2,000 MB free on the card and 2,824 MB allowed: **the card had no room for it" in report

    record["holder"]["shown"] = True
    record["overrun"]["fallbacks"] = row("cuda", 1, 0, 6550.0)
    record["overrun"]["retry"] = row("cuda", 0, 1, 6550.0)
    report = bench_fit.render(record)
    assert "- Shown: yes" in report and "did not make the expected change" not in report
    assert "not exercised" not in report and "not shown" not in report and "no room" not in report
    assert report.count("torch refused 2,287 MB with 6,550 MB free on the card") == 2
    assert report.count("the cap refused it, so the driver was never asked") == 2


def test_settings_are_typed_from_the_manifest_and_mistakes_named(
    tmp_path: Path, make_node: MakeNode, node_root: Path
) -> None:
    make_node(SMALL_NODE, CODE)
    manifest = discover([node_root]).get("test.bench")
    assert bench_fit.parse_sets(["resolution=512", "fast=true", "precision=fp32"], manifest) == {
        "resolution": 512,
        "fast": True,
        "precision": "fp32",
    }
    with pytest.raises(ValueError, match="no parameter 'steps'") as info:
        bench_fit.parse_sets(["steps=4", "resolution=lots", "resolution=99999"], manifest)
    assert "not a int" in str(info.value) and "above its maximum" in str(info.value)


def test_an_estimate_holds_within_ten_percent_or_64_mb() -> None:
    assert bench_fit.holds(1000, 1090) and bench_fit.holds(100, 160) and not bench_fit.holds(1000, 1120)
    assert bench_fit.holds(None, 10) is None and bench_fit.holds(10, None) is None


def test_ac8_is_described_by_its_node_folder_and_sized_from_the_nodes_own_model() -> None:
    plan = bench_fit.find_ac8()
    assert plan.node == "test.vram" and (plan.folder / "node.json").is_file()
    manifest = discover([plan.folder.parent]).get(plan.node)
    for total, free in ((4295, 3900), (8590, 7000), (25770, 24500), (85899, 84000)):
        card = {"index": 0, "capability": "8.6", "vram_total_mb": total, "vram_free_mb": free}
        budget = free - max(1611, 0.1 * total)
        settings = bench_fit.three_settings(manifest, plan, budget, card)
        values = [s[plan.scale] for s in settings]
        assert values == sorted(values) and all(v % 64 == 0 for v in values)
        for share, params in zip(plan.shares, settings, strict=True):
            need = bench_fit._need(manifest.memory, {**bench_fit._defaults(manifest), **params}, card)  # type: ignore[arg-type]
            assert need is not None
            assert need <= share * budget or params[plan.scale] == manifest.params[plan.scale].minimum
    small = bench_fit.three_settings(manifest, plan, 3000, {"vram_total_mb": 4295})
    large = bench_fit.three_settings(manifest, plan, 70000, {"vram_total_mb": 85899})
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
    report = bench_fit.render(record)
    assert "**Context: not measured**" in report and "Not measured: nvidia-smi was not found" in report
    assert "Not measured: The card's free memory could not be read." in report
    assert report.count("**not shown**") == 2  # the overruns' events carry no message here
    assert " 0 MB" not in report  # a number not measured is never written as zero
    assert bench_fit.render({"node": "test.vram", "why": "no card"}).count("Not measured: no card") == 1


def test_the_report_is_named_by_date_and_card_in_a_folder(tmp_path: Path) -> None:
    record = {"date": "2026-10-01T12:00:00+00:00", "card": {"name": "NVIDIA GeForce Something 9000"}}
    assert bench_fit.report_path(tmp_path, record) == tmp_path / "2026-10-01-something-9000-fit.md"
    assert bench_fit.report_path(tmp_path / "mine.md", record) == tmp_path / "mine.md"


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
    assert bench_fit.cap_refusal(CAPPED) == {
        "request_mb": round(2.13 * gib, 1),
        "card_free_mb": round(6.10 * gib, 1),
        "allowed_mb": round(2.63 * gib, 1),
    }
    full = CAPPED.replace("of which 6.10 GiB is free", "of which 512.00 MiB is free")
    assert bench_fit.cap_refusal(full)["card_free_mb"] == round(512 * 1024**2 / 1e6, 1)  # type: ignore[index]
    # Without a cap, or not torch's message at all, it shows nothing.
    assert bench_fit.cap_refusal(CAPPED.replace("2.63 GiB allowed; ", "")) is None
    assert bench_fit.cap_refusal("CUDA out of memory") is None and bench_fit.cap_refusal(None) is None
