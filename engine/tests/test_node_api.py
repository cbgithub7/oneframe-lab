"""The node API (spec 009): a job's JSON, written and read in one module, and contexts that show
exactly their Protocols."""

from __future__ import annotations

import json
import typing
from pathlib import Path
from typing import Any

import pytest

from oneframe.node import Context, LoadContext, Value
from oneframe.node.job import Done, Job
from oneframe.node.runner import run_job

# A node that says, as a stage named for the function, the public members of the context it got.
MEMBERS = """
import json

def _public(ctx):
    return json.dumps(sorted(name for name in dir(ctx) if not name.startswith("_")))

def load(ctx):
    ctx.stage("load", _public(ctx))

def run(ctx, state=None):
    ctx.stage("run", _public(ctx))
"""


@pytest.mark.parametrize(
    ("function", "protocol", "load"),
    [("run", Context, None), ("load", LoadContext, "node.py:load")],
    ids=["run", "load"],
)
def test_a_context_shows_exactly_its_protocol(
    tmp_path: Path, function: str, protocol: Any, load: str | None
) -> None:
    folder = tmp_path / "node"
    folder.mkdir()
    (folder / "node.py").write_text(MEMBERS, encoding="utf-8")
    events: list[dict[str, Any]] = []
    job = Job(node="test.members", folder=folder, entry="node.py:run", out_dir=tmp_path / "out", load=load)
    run_job(job, events.append)
    shown = {e["stage"]: json.loads(e["message"]) for e in events if e["event"] == "stage"}
    assert shown[function] == sorted(typing.get_protocol_members(protocol))


def test_a_job_survives_its_json(tmp_path: Path) -> None:
    job = Job(
        node="test.job",
        folder=tmp_path / "node",
        entry="node.py:run",
        out_dir=tmp_path / "out",
        run="r1",
        step="s",
        inputs={
            "image": Value("Image", tmp_path / "a.png", {"space": "srgb"}, {"w": 4}, "measured", "k:image")
        },
        params={"resolution": 512, "mode": "fast"},
        device="cuda",
        files={"weights": str(tmp_path / "w.safetensors")},
        attempt=2,
        precision="fp16",
        memory_budget_mb=5389.0,
        vram_cap_mb=5000.0,
        outside_torch_mb=100.0,
    )
    back = Job.from_json(job.to_json())
    assert back.inputs["image"].to_json() == job.inputs["image"].to_json()
    assert {**vars(back), "inputs": None} == {**vars(job), "inputs": None}
    # A param that is not plain JSON is written as its `str`, never refused.
    odd = Job(node="n", folder=tmp_path, entry="node.py:run", out_dir=tmp_path, params={"where": tmp_path})
    assert Job.from_json(odd.to_json()).params == {"where": str(tmp_path)}
    done = Done(
        outputs={"text": {"path": "t.json", "facets": {}, "meta": {}}},
        seconds=1.5,
        peaks={"peak_reserved_mb": 3000.0, "peak_vram_mb": 2500.0, "peak_ram_mb": None},
        ways={"decode": "tiled"},
        stats={"n": 1},
    )
    assert Done.from_event(done.to_event()) == done
