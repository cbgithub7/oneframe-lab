"""Nodes that run in a child process: the protocol survives what model code does to stdout, the
network stays closed, failures keep their kind, and Stop kills the child."""

from __future__ import annotations

import _thread
import json
import os
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from conftest import Events, MakeNode

import oneframe
from oneframe.environment import child_env
from oneframe.executors import (
    EngineExecutor,
    NodeError,
    ProcessExecutor,
    child_command,
    died_message,
)
from oneframe.graph import Graph
from oneframe.node.job import Job
from oneframe.processes import alive, wait_gone
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


HANG = "import os, time\ndef run(ctx):\n    ctx.stage('hang', str(os.getpid()))\n    time.sleep(60)\n"


def test_stop_kills_a_runtime_child(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(make_node, "test.hang", HANG)
    started = time.monotonic()
    stop = threading.Event()

    def listen(event: dict[str, Any]) -> None:
        events.append(event)
        if event["event"] == "stage":
            stop.set()

    result = scheduler_for().run(_one("test.hang"), listen, stop)
    assert result.status == "stopped"
    assert time.monotonic() - started < 20
    # The interpreter itself, not only the process the engine started (on Windows a venv's
    # python.exe is a launcher that starts the interpreter as another process): gone on return.
    assert not alive(int(events.of("stage")[0]["message"]))


# A node's body that starts a process of its own, says its id and its own, and hangs.
STARTS = (
    "import subprocess, sys, time\n"
    "sleeper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "ctx.stage('started', f'{sleeper.pid} {os.getpid()}')\n"
    "time.sleep(60)\n"
)


def test_stop_ends_the_processes_a_node_started(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(make_node, "test.starts", "import os\ndef run(ctx):\n" + textwrap.indent(STARTS, "    "))
    stop = threading.Event()

    def listen(event: dict[str, Any]) -> None:
        events.append(event)
        if event["event"] == "stage":
            stop.set()

    result = scheduler_for().run(_one("test.starts"), listen, stop)
    assert result.status == "stopped"
    sleeper, node = (int(pid) for pid in events.of("stage")[0]["message"].split())
    assert not alive(node) and wait_gone(sleeper)


def test_ctrl_c_during_a_run_ends_its_processes(tmp_path: Path) -> None:
    """In a group of its own, a node's process no longer hears a terminal's Ctrl+C; the tool's
    KeyboardInterrupt ends it, and what it started, through the same kill as a Stop."""
    seen: list[dict[str, object]] = []

    def listen(event: dict[str, object]) -> None:
        seen.append(event)
        if event["event"] == "stage":
            _thread.interrupt_main()  # what Ctrl+C does to the tool's main thread

    with pytest.raises(KeyboardInterrupt):
        ProcessExecutor(Path(sys.executable)).execute(
            _executor_job(tmp_path, STARTS),
            listen,
            threading.Event(),
        )
    sleeper, node = (int(pid) for pid in str(seen[0]["message"]).split())
    assert not alive(node) and wait_gone(sleeper)


def test_a_runtime_child_is_told_to_end_with_the_engine(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    _runtime_node(
        make_node,
        "test.told",
        "import sys\ndef run(ctx):\n    ctx.stage('told', str('--exit-with-parent' in sys.argv))\n" + WRITE,
    )
    result = scheduler_for().run(_one("test.told"), events.append)
    assert result.status == "done", result.error
    assert events.of("stage")[0]["message"] == "True"


def test_a_child_ends_when_the_engine_is_gone(tmp_path: Path) -> None:
    """A killed engine cannot stop its children; the child sees its stdin close and ends itself."""
    job = _executor_job(tmp_path, "ctx.stage('hang', str(os.getpid()))\nimport time\ntime.sleep(60)\n")
    job_path = tmp_path / "job.json"
    job_path.write_text(job.to_json(), encoding="utf-8")
    proc = subprocess.Popen(
        child_command(Path(sys.executable), job_path, exit_with_parent=True),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        env=child_env(None, {}),
        text=True,
        encoding="utf-8",
    )
    try:
        assert proc.stdout is not None and proc.stdin is not None
        pid = None
        for line in proc.stdout:
            event = json.loads(line)
            if event.get("event") == "stage":
                pid = int(event["message"])
                break
        assert pid is not None
        proc.stdin.close()  # what the engine's death looks like from here
        assert proc.wait(timeout=10) == 1
        assert wait_gone(pid)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


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


def _executor_job(tmp_path: Path, body: str) -> Job:
    code = tmp_path / "node" / "node.py"
    code.parent.mkdir(parents=True, exist_ok=True)
    code.write_text(
        "import os, json\n\n\ndef run(ctx):\n" + textwrap.indent(textwrap.dedent(body), "    "),
        encoding="utf-8",
    )
    return Job(node="test.exec", folder=code.parent, entry="node.py:run", out_dir=tmp_path / "out")


def test_a_runtime_child_cannot_import_the_engines_own_modules(tmp_path: Path) -> None:
    """The child's file lies among the engine's modules; a node's `import models` must reach the
    runtime's package of that name, never the engine's. Of the engine, node code reaches the node
    API alone, even on the engine's own Python, where `oneframe` is installed."""
    body = """
    import importlib.util, sys
    from oneframe.node import Value
    names = ("oneframe.scheduler", "models", "graph", "errors", "layout", "scheduler", "child")
    found = [n for n in names if importlib.util.find_spec(n) is not None]
    on_path = sys.argv[0].rsplit(os.sep, 1)[0] in sys.path
    ctx.stage("path", json.dumps({"found": found, "on_path": on_path, "api": Value.__module__}))
    """
    seen: list[dict[str, object]] = []
    ProcessExecutor(Path(sys.executable)).execute(
        _executor_job(tmp_path, body), seen.append, threading.Event()
    )
    assert json.loads(str(seen[0]["message"])) == {"found": [], "on_path": False, "api": "oneframe.node"}


def test_two_engine_nodes_with_same_named_helpers_each_run_their_own(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """Each node's folder is a package of its own: two `utils.py` never meet, and the engine's
    import path is left as it was."""
    for name in ("first", "second"):
        folder = make_node(
            {
                "id": f"test.{name}",
                "version": "1",
                "title": name,
                "category": "test",
                "outputs": {"text": "Text"},
                "run": {"where": "engine", "entry": "node.py:run"},
            },
            "from . import utils\n"
            "def run(ctx):\n"
            "    p = ctx.path('t.txt'); p.write_text(utils.WORD, encoding='utf-8'); ctx.output('text', p)\n",
        )
        (folder / "utils.py").write_text(f"WORD = {name!r}\n", encoding="utf-8")
    graph = Graph.from_json(
        {"version": 1, "nodes": {"a": {"node": "test.first"}, "b": {"node": "test.second"}}, "edges": []}
    )
    path = list(sys.path)
    result = scheduler_for().run(graph, events.append)
    assert result.status == "done", result.error
    words = {step: outs["text"].path.read_text(encoding="utf-8") for step, outs in result.outputs.items()}
    assert words == {"a": "first", "b": "second"}
    assert sys.path == path


def test_a_runtime_nodes_helper_never_hides_a_package_of_its_runtime(tmp_path: Path) -> None:
    """A helper beside the node is reached relatively; `import utils` is the runtime's own."""
    site = tmp_path / "site" / "utils"
    site.mkdir(parents=True)
    (site / "__init__.py").write_text("WORD = 'the runtime'\n", encoding="utf-8")
    body = """
    from . import utils as mine
    import utils as theirs
    ctx.stage("utils", json.dumps([mine.WORD, theirs.WORD]))
    """
    job = _executor_job(tmp_path, body)
    (job.folder / "utils.py").write_text("WORD = 'the node'\n", encoding="utf-8")
    seen: list[dict[str, object]] = []
    ProcessExecutor(Path(sys.executable), env={"PYTHONPATH": str(site.parent)}).execute(
        job, seen.append, threading.Event()
    )
    assert json.loads(str(seen[0]["message"])) == ["the node", "the runtime"]


def test_an_engine_run_leaves_its_sockets_alone(
    make_node: MakeNode, scheduler_for: Callable[..., Scheduler], events: Events
) -> None:
    """A runtime child's guards are never put up in the engine's process: its own downloads stay
    open, and its `oneframe` stays the real package."""
    connect, connect_ex = socket.socket.connect, socket.socket.connect_ex
    make_node(
        {
            "id": "test.sockets",
            "version": "1",
            "title": "sockets",
            "category": "test",
            "outputs": {"text": "Text"},
            "run": {"where": "engine", "entry": "node.py:run"},
        },
        "import socket\ndef run(ctx):\n    ctx.stage('connect', repr(socket.socket.connect))\n" + WRITE,
    )
    result = scheduler_for().run(_one("test.sockets"), events.append)
    assert result.status == "done", result.error
    assert events.of("stage")[0]["message"] == repr(connect)
    assert socket.socket.connect is connect and socket.socket.connect_ex is connect_ex
    assert sys.modules["oneframe"] is oneframe and oneframe.__file__ is not None


def test_a_node_fails_in_its_own_words(tmp_path: Path) -> None:
    """ctx.fail ends the run with kind `node` and the message as the node wrote it, in a runtime
    child and in the engine's process alike."""
    body = "ctx.fail('The photo is 32 pixels wide; this node needs at least 256.')\n"
    words = "The photo is 32 pixels wide; this node needs at least 256."
    for executor in (ProcessExecutor(Path(sys.executable)), EngineExecutor()):
        with pytest.raises(NodeError) as info:
            executor.execute(_executor_job(tmp_path, body), lambda _e: None, threading.Event())
        assert (info.value.kind, str(info.value)) == ("node", words)


def test_any_other_error_is_reported_with_its_type(tmp_path: Path) -> None:
    """An error the node did not word for the person keeps its type, so whoever reads it knows what
    went wrong, in a runtime child and in the engine's process alike."""
    body = "raise ValueError('expected 3 channels, got 4')\n"
    for executor in (ProcessExecutor(Path(sys.executable)), EngineExecutor()):
        with pytest.raises(NodeError) as info:
            executor.execute(_executor_job(tmp_path, body), lambda _e: None, threading.Event())
        assert (info.value.kind, str(info.value)) == ("error", "ValueError: expected 3 channels, got 4")


def test_a_failure_carries_the_peaks_of_the_run(tmp_path: Path) -> None:
    body = """
    class OutOfMemoryError(RuntimeError):
        pass
    raise OutOfMemoryError("CUDA out of memory")
    """
    with pytest.raises(NodeError) as info:
        ProcessExecutor(Path(sys.executable)).execute(
            _executor_job(tmp_path, body), lambda _e: None, threading.Event()
        )
    assert info.value.kind == "oom"
    assert set(info.value.peaks) == {"peak_reserved_mb", "peak_vram_mb", "peak_ram_mb"}


def test_a_run_is_told_hubs_are_offline_and_gets_no_hub_token(tmp_path: Path) -> None:
    """Hub libraries read only what is on disk, no token or endpoint reaches a run, and torch loads
    with weights_only: whatever the person's environment or the runtime's definition says."""
    names = (
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD",
        "HF_TOKEN",
        "hf_token",
        "HF_TOKEN_PATH",
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
        "HF_TOKEN_PATH": "/home/someone/.cache/huggingface/token",
        "hf_token": "hf_secret",  # one variable on Windows, whatever its case
        "KEEP": "1",
    }
    seen: list[dict[str, object]] = []
    executor = ProcessExecutor(
        Path(sys.executable),
        env={"TRANSFORMERS_OFFLINE": "0", "HF_TOKEN": "hf_runtime", "hf_hub_offline": "0"},
        base_env=person,
    )
    executor.execute(_executor_job(tmp_path, body), seen.append, threading.Event())
    assert json.loads(str(seen[0]["message"])) == {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "TORCH_FORCE_WEIGHTS_ONLY_LOAD": "1",
        "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": None,
        "HF_TOKEN": None,
        "hf_token": None,
        "HF_TOKEN_PATH": os.devnull,  # a token file that holds nothing, not the person's own
        "HUGGING_FACE_HUB_TOKEN": None,
        "HF_ENDPOINT": None,
        "KEEP": "1",
    }


def test_a_node_that_reads_stdin_gets_end_of_file_at_once(tmp_path: Path) -> None:
    """The child's stdin is the pipe it watches to end with its parent; node code must not wait on it."""
    body = "try:\n    input()\nexcept EOFError:\n    ctx.stage('stdin', 'eof')\n"
    seen: list[dict[str, object]] = []
    started = time.monotonic()
    ProcessExecutor(Path(sys.executable)).execute(
        _executor_job(tmp_path, body), seen.append, threading.Event()
    )
    assert seen[0]["message"] == "eof"
    assert time.monotonic() - started < 20


def test_a_node_that_queries_its_stdin_does_not_hang(
    tmp_path: Path, stop_after: Callable[[float], threading.Event]
) -> None:
    """October's hang, as inferred: the C runtime of a DLL numpy loads asks at start what its standard
    input is, and on Windows that waited behind the child's watcher, reading the same pipe. The node
    asks the same; a hang come back is stopped at the deadline and fails, rather than hanging."""
    body = """
    import stat, sys
    answers = {"mode": stat.S_IFMT(os.fstat(0).st_mode), "tty": os.isatty(0)}
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32")
        kernel32.GetStdHandle.restype = wintypes.HANDLE
        kernel32.GetFileType.argtypes = (wintypes.HANDLE,)
        answers["type"] = kernel32.GetFileType(kernel32.GetStdHandle(-10))  # STD_INPUT_HANDLE
    ctx.stage("stdin", json.dumps(answers))
    """
    seen: list[dict[str, object]] = []
    ProcessExecutor(Path(sys.executable)).execute(_executor_job(tmp_path, body), seen.append, stop_after(60))
    answers = json.loads(str(seen[0]["message"]))
    # The null device, not the pipe the watcher reads: a character device, FILE_TYPE_CHAR on Windows,
    # whose C runtime counts every character device, the null one too, as a terminal.
    assert answers["mode"] == stat.S_IFCHR and answers["tty"] is (sys.platform == "win32")
    assert answers.get("type", 2) == 2


def test_a_listener_is_told_the_childs_process_id(tmp_path: Path) -> None:
    pids: list[int] = []
    seen: list[dict[str, object]] = []
    body = "ctx.stage('pid', str(os.getpid()))\n"
    ProcessExecutor(Path(sys.executable), on_pid=pids.append).execute(
        _executor_job(tmp_path, body), seen.append, threading.Event()
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
            _executor_job(tmp_path, body), lambda _e: None, threading.Event()
        )
    assert info.value.kind == "died" and "may have run out of system memory" in str(info.value)
