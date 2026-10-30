"""`bench:fit`'s plumbing, on the processor: a node run at given settings, each estimate beside the
peak it measured, and the report. Spec 002's check on a card builds on it (test_fit_check.py)."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import HostRuntimes, MakeNode

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
    scheduler = bench_fit.make_scheduler(None, registry, HostRuntimes(), tmp_path / "cache")
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
    scheduler = bench_fit.make_scheduler(None, registry, HostRuntimes(), tmp_path / "cache")
    groups = [{"resolution": 800, "extra": 200}, {"resolution": 800}, {"resolution": 800}]
    rows = bench_fit.bench_node(scheduler, "test.bench", groups)["runs"]
    assert [r["status"] for r in rows] == ["done", "done", "done"], [r["error"] for r in rows]
    assert [r["estimate_mb"] for r in rows] == [140, 140, 140]
    assert [f["estimate"] for r in rows for f in r["fits"]] == ["known", "known", "known"]


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


def test_the_report_is_named_by_date_and_card_in_a_folder(tmp_path: Path) -> None:
    record = {"date": "2026-10-01T12:00:00+00:00", "card": {"name": "NVIDIA GeForce Something 9000"}}
    assert bench_fit.report_path(tmp_path, record) == tmp_path / "2026-10-01-something-9000-fit.md"
    assert bench_fit.report_path(tmp_path / "mine.md", record) == tmp_path / "mine.md"
