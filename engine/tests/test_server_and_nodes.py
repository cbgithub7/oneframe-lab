from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from oneframe import BUILTIN_NODES_DIR
from oneframe.cache import Cache
from oneframe.graph import Graph
from oneframe.registry import discover
from oneframe.scheduler import Scheduler


def test_importing_the_server_loads_no_numeric_or_model_library() -> None:
    probe = (
        "import sys, oneframe.server\n"
        "heavy = [m for m in ('numpy', 'PIL', 'torch', 'cv2', 'transformers') if m in sys.modules]\n"
        "print(','.join(heavy))"
    )
    out = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


class Client:
    def __init__(self, data: Path):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "oneframe.server", "--data", str(data)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        self.next_id = 0
        self.events: list[dict[str, Any]] = []
        ready = self._read()
        assert ready["event"] == "engine.ready"

    def _read(self) -> dict[str, Any]:
        assert self.proc.stdout is not None
        return json.loads(self.proc.stdout.readline())

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        assert self.proc.stdin is not None
        self.next_id += 1
        self.proc.stdin.write(
            json.dumps({"id": self.next_id, "method": method, "params": params or {}}) + "\n"
        )
        self.proc.stdin.flush()
        while True:
            msg = self._read()
            if msg.get("id") == self.next_id:
                return msg
            self.events.append(msg)

    def wait_for(self, kinds: set[str], timeout: float = 30) -> dict[str, Any]:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            for e in self.events:
                if e.get("event") in kinds:
                    return e
            self.events.append(self._read())
        raise TimeoutError(kinds)

    def close(self) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.close()
        self.proc.wait(timeout=10)


def _photo(path: Path, focal_35: int | None = 26) -> Path:
    image = Image.new("RGB", (8, 6), (200, 100, 50))
    exif = Image.Exif()
    if focal_35:
        exif.get_ifd(0x8769)[0xA405] = focal_35  # Exif IFD, FocalLengthIn35mmFilm
    image.save(path, exif=exif)
    return path


def test_the_server_answers_and_runs_a_graph(tmp_path: Path) -> None:
    client = Client(tmp_path / "data")
    try:
        hello = client.call("engine.hello")["result"]
        assert hello["python"].startswith("3.14") and "graph.run" in hello["methods"]
        listed = client.call("nodes.list")["result"]
        assert {n["id"] for n in listed["nodes"]} >= {"source.image", "convert.depth_to_points"}
        assert "Depth" in client.call("ports.list")["result"]["types"]
        assert "Unknown method" in client.call("nope")["error"]["message"]

        bad = client.call(
            "graph.run", {"graph": {"version": 1, "nodes": {"x": {"node": "no.such"}}, "edges": []}}
        )
        assert bad["error"]["problems"][0]["node"] == "x"

        photo = _photo(tmp_path / "p.jpg")
        graph = {
            "version": 1,
            "nodes": {"p": {"node": "source.image", "params": {"path": str(photo)}}},
            "edges": [],
        }
        run = client.call("graph.run", {"graph": graph})["result"]["run"]
        done = client.wait_for({"run.done", "run.failed"})
        assert done["event"] == "run.done" and done["run"] == run
        image = done["outputs"]["p"]["image"]
        assert image["facets"] == {"alpha": "none"} and image["trust"] == "measured"
        assert image["meta"]["focal_px_exif"] == round(26 / 36 * 8, 2)
    finally:
        client.close()


def test_depth_to_points_unprojects_through_the_pinhole(tmp_path: Path) -> None:
    sched = Scheduler(discover([BUILTIN_NODES_DIR, tmp_path / "extra"]), Cache(tmp_path / "cache"))
    extra = tmp_path / "extra" / "test.depth_and_camera"
    extra.mkdir(parents=True)
    (extra / "node.json").write_text(
        json.dumps(
            {
                "id": "test.depth_and_camera",
                "version": "1",
                "title": "t",
                "category": "test",
                "outputs": {
                    "depth": "Depth[kind=metric, measure=z]",
                    "camera": "Camera[model=pinhole, source=given]",
                },
            }
        ),
        encoding="utf-8",
    )
    (extra / "node.py").write_text(
        "import json, numpy as np\n"
        "def run(ctx):\n"
        "    d = np.full((2, 4), 2.0, dtype=np.float32); d[0, 0] = 0\n"
        "    p = ctx.path('d.npz'); np.savez(p, depth=d); ctx.output('depth', p)\n"
        "    c = ctx.path('c.json'); c.write_text(json.dumps({'fx': 2, 'fy': 2, 'cx': 2, 'cy': 1}))\n"
        "    ctx.output('camera', c)\n",
        encoding="utf-8",
    )
    sched.registry = discover([BUILTIN_NODES_DIR, tmp_path / "extra"])
    graph = Graph.from_json(
        {
            "version": 1,
            "nodes": {"s": {"node": "test.depth_and_camera"}, "u": {"node": "convert.depth_to_points"}},
            "edges": [{"from": "s.depth", "to": "u.depth"}, {"from": "s.camera", "to": "u.camera"}],
        }
    )
    result = sched.run(graph, lambda e: None)
    assert result.status == "done", result.error
    data = np.load(result.outputs["u"]["points"].path)
    points, valid = data["points"], data["valid"]
    assert points.shape == (2, 4, 3)
    assert not valid[0, 0] and valid[1, 3]
    # pixel (u=3, v=1) has centre (3.5, 1.5): X = (3.5 - 2) * 2 / 2, Y = (1.5 - 1) * 2 / 2, Z = 2
    assert np.allclose(points[1, 3], [1.5, 0.5, 2.0])
    assert result.outputs["u"]["points"].facets == {"frame": "camera", "scale": "metric"}
