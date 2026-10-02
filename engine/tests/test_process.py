"""Nodes that run in a child process: the protocol survives what model code does to stdout, the
network stays closed, failures keep their kind, and Stop kills the child."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from conftest import Events, MakeNode

from oneframe.executors import NodeError, ProcessExecutor, died_message
from oneframe.graph import Graph
from oneframe.scheduler import RuntimeMissing, Scheduler


def _runtime_node(make_node: MakeNode, node_id: str, code: str, **extra: object) -> None:
    manifest: dict[str, object] = {
        "id": node_id,
        "version": "1",
        "title": node_id,
        "category": "test",
        "outputs": {"text": "Text"},
        "run": {"where": "runtime", "runtime": "test", "entry": "node.py:run"},
    }
    manifest.update(extra)
    make_node(manifest, code)


def _one(node_id: str) -> Graph:
    return Graph.from_json({"version": 1, "nodes": {"n": {"node": node_id}}, "edges": []})


WRITE = "    p = ctx.path('t.json'); p.write_text('1'); ctx.output('text', p)\n"


def test_printing_code_cannot_corrupt_the_protocol(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.noisy",
        "import os, sys\n"
        "def run(ctx):\n"
        '    print(\'{"event": "done", "fake": true}\')\n'
        "    os.write(1, b'raw bytes on fd 1\\n')\n"
        "    sys.stdout.write('more noise\\n')\n"
        "    ctx.stage('work', 'doing it')\n"
        "    for i in range(3):\n"
        "        ctx.progress(i + 1, 3)\n" + WRITE,
    )
    result = scheduler_for().run(_one("test.noisy"), events.append)
    assert result.status == "done", result.error
    assert [e["done"] for e in events.of("progress")] == [1, 2, 3]
    assert events.of("stage")[0]["message"] == "doing it"
    assert all(e.get("step") == "n" for e in events.of("progress"))


def test_the_network_is_closed_during_a_run(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.net",
        "import socket\ndef run(ctx):\n    socket.create_connection(('203.0.113.7', 443), timeout=2)\n"
        + WRITE,
    )
    result = scheduler_for().run(_one("test.net"), events.append)
    assert result.status == "failed" and result.error is not None
    assert result.error["kind"] == "fetch"
    assert "203.0.113.7" in result.error["message"]


def test_a_child_that_dies_is_reported_with_its_stderr(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.die",
        "import os, sys\ndef run(ctx):\n    sys.stderr.write('last words\\n'); sys.stderr.flush()\n"
        "    os._exit(3)\n",
    )
    result = scheduler_for().run(_one("test.die"), events.append)
    assert result.error is not None and result.error["kind"] == "died"
    assert "code 3" in result.error["message"]
    assert "last words" in result.error["detail"]


def test_stop_kills_a_runtime_child(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node, "test.hang", "import time\ndef run(ctx):\n    ctx.stage('hang')\n    time.sleep(60)\n"
    )
    started = time.monotonic()

    def stop_when_hanging() -> bool:
        return any(e["event"] == "stage" for e in events)

    result = scheduler_for().run(_one("test.hang"), events.append, stop_when_hanging)
    assert result.status == "stopped"
    assert time.monotonic() - started < 20


def test_a_missing_runtime_is_its_own_failure(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(make_node, "test.needs_runtime", "def run(ctx):\n" + WRITE)

    def none(runtime: str) -> object:
        raise RuntimeMissing(f"The runtime {runtime!r} is not installed.")

    result = scheduler_for(runtime_python=none).run(_one("test.needs_runtime"), events.append)
    assert result.error is not None and result.error["kind"] == "runtime"


@pytest.mark.usefixtures("basic_nodes")
def test_engine_and_runtime_nodes_mix_in_one_graph(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.remote_depth",
        "import numpy as np\n"
        "def run(ctx):\n"
        "    size = len(ctx.inputs['image'].path.read_bytes())\n"
        "    p = ctx.path('d.npz'); np.savez(p, depth=np.ones((2, 2)) * size)\n"
        "    ctx.output('depth', p, facets={'kind': 'metric'})\n",
        inputs={"image": "Image"},
        outputs={"depth": "Depth[measure=z]"},
    )
    g = Graph.from_json(
        {
            "version": 1,
            "nodes": {"i": {"node": "test.image"}, "r": {"node": "test.remote_depth"}},
            "edges": [{"from": "i.image", "to": "r.image"}],
        }
    )
    result = scheduler_for().run(g, events.append)
    assert result.status == "done", result.error
    assert result.outputs["r"]["depth"].facets == {"kind": "metric", "measure": "z"}


# -- spec 002: what the executor carries ---------------------------------------------------------


def _executor_job(tmp_path: Path, body: str, **extra: object) -> dict[str, object]:
    code = tmp_path / "node" / "node.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        "import os, json\n\n\ndef run(ctx):\n" + textwrap.indent(textwrap.dedent(body), "    "),
        encoding="utf-8",
    )
    return {
        "node": "test.exec",
        "entry": {"file": str(code), "function": "run"},
        "out_dir": str(tmp_path / "out"),
        "device": "cpu",
        **extra,
    }


def test_a_failure_carries_the_peaks_of_the_run(tmp_path: Path) -> None:
    body = """
    class OutOfMemoryError(RuntimeError):
        pass
    raise OutOfMemoryError("CUDA out of memory")
    """
    with pytest.raises(NodeError) as info:
        ProcessExecutor(Path(sys.executable)).execute(
            _executor_job(tmp_path, body), lambda _e: None, lambda: False
        )
    assert info.value.kind == "oom"
    assert set(info.value.peaks) == {"peak_reserved_mb", "peak_vram_mb", "peak_ram_mb"}


def test_a_job_brings_its_own_environment(tmp_path: Path) -> None:
    body = "ctx.stage('env', os.environ.get('CUDA_VISIBLE_DEVICES', 'unset'))\n"
    seen: list[dict[str, object]] = []
    executor = ProcessExecutor(Path(sys.executable), env={"CUDA_VISIBLE_DEVICES": "0", "KEEP": "1"})
    executor.execute(
        _executor_job(tmp_path, body, env={"CUDA_VISIBLE_DEVICES": "-1"}), seen.append, lambda: False
    )
    executor.execute(_executor_job(tmp_path, body), seen.append, lambda: False)
    assert [e["message"] for e in seen] == ["-1", "0"]  # a processor job sees no card; the next is unchanged


def test_a_run_is_told_hubs_are_offline_and_gets_no_hub_token(tmp_path: Path) -> None:
    """Hub libraries read only what is on disk, no token or endpoint reaches a run, and torch loads
    with weights_only: whatever the person's environment or the runtime's definition says."""
    names = (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "HF_ENDPOINT",
        "KEEP",
    )
    body = f"ctx.stage('env', json.dumps({{n: os.environ.get(n) for n in {names!r}}}))\n"
    person = {
        **os.environ,
        "HF_HUB_OFFLINE": "0",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
        "HF_TOKEN": "hf_secret",
        "HUGGING_FACE_HUB_TOKEN": "hf_secret",
        "HF_ENDPOINT": "http://127.0.0.1:9",
        "KEEP": "1",
    }
    seen: list[dict[str, object]] = []
    executor = ProcessExecutor(
        Path(sys.executable), env={"TRANSFORMERS_OFFLINE": "0", "HF_TOKEN": "hf_runtime"}, base_env=person
    )
    executor.execute(_executor_job(tmp_path, body), seen.append, lambda: False)
    assert json.loads(str(seen[0]["message"])) == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": None,
        "HF_TOKEN": None,
        "HUGGING_FACE_HUB_TOKEN": None,
        "HF_ENDPOINT": None,
        "KEEP": "1",
    }


def test_a_listener_is_told_the_childs_process_id(tmp_path: Path) -> None:
    pids: list[int] = []
    seen: list[dict[str, object]] = []
    body = "ctx.stage('pid', str(os.getpid()))\n"
    ProcessExecutor(Path(sys.executable), on_pid=pids.append).execute(
        _executor_job(tmp_path, body), seen.append, lambda: False
    )
    assert len(pids) == 1 and str(pids[0]) == seen[0]["message"]


@pytest.mark.parametrize(
    ("code", "platform", "memory"),
    [
        (-9, "linux", True),
        (0xC0000017, "win32", True),
        (0xC000012D, "win32", True),
        (3, "linux", False),
        (-9, "win32", False),
        (0xC0000005, "win32", False),
    ],
)
def test_a_death_that_may_be_lack_of_memory_says_so(code: int, platform: str, memory: bool) -> None:
    text = died_message(code, platform)
    assert f"code {code}" in text and ("may have run out of system memory" in text) == memory


@pytest.mark.skipif(sys.platform != "linux", reason="SIGKILL, as the kernel's out-of-memory killer sends it")
def test_a_child_killed_as_the_oom_killer_does_is_reported_as_maybe_out_of_memory(tmp_path: Path) -> None:
    body = "import signal\nos.kill(os.getpid(), signal.SIGKILL)\n"
    with pytest.raises(NodeError) as info:
        ProcessExecutor(Path(sys.executable)).execute(
            _executor_job(tmp_path, body), lambda _e: None, lambda: False
        )
    assert info.value.kind == "died" and "may have run out of system memory" in str(info.value)
