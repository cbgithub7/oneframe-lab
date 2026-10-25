"""The one module that starts, reads and ends processes (spec 008): every process in its own group
with no window and read as UTF-8, a Stop that ends a whole tree even after its output has closed,
and a kill that waits for what it is told of and never raises."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any

import pytest

from oneframe import processes
from oneframe.errors import Stopped
from oneframe.processes import Child, alive, wait_gone

# Starts a grandchild that sleeps, says both ids, then closes its output and sleeps itself.
STEP = """
import os, subprocess, sys, time
grandchild = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
)
print(os.getpid(), grandchild.pid, flush=True)
os.close(1)
os.close(2)
time.sleep(60)
"""


def test_stop_during_a_step_ends_its_whole_tree() -> None:
    stop = threading.Event()
    pids: list[int] = []

    def on_line(line: str) -> None:
        pids.extend(int(pid) for pid in line.split())
        threading.Timer(1.0, stop.set).start()  # once the step has closed its output

    with pytest.raises(Stopped):
        processes.run([sys.executable, "-c", STEP], env=dict(os.environ), on_line=on_line, stop=stop)
    step, grandchild = pids
    assert wait_gone(step) and wait_gone(grandchild)


_STARTS: list[dict[str, Any]] = []


class _Spy(subprocess.Popen[str]):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _STARTS.append(kwargs)
        super().__init__(*args, **kwargs)


def test_every_start_gets_its_own_group_and_no_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(subprocess, "Popen", _Spy)
    _STARTS.clear()
    # Its group's and its session's ids where the system has them, and bytes that are not UTF-8.
    says = (
        "import os, sys\n"
        "group, session = getattr(os, 'getpgid', abs)(0), getattr(os, 'getsid', abs)(0)\n"
        "print(os.getpid(), group, session)\n"
        "sys.stdout.flush()\n"
        "os.write(1, 'é '.encode() + bytes([255]) + b'\\n')\n"
    )
    argv = [sys.executable, "-c", says]
    with Child.start(argv, env=dict(os.environ)) as child:
        seen = list(child.lines(threading.Event()))
    lines: list[str] = []
    processes.run(argv, env=dict(os.environ), on_line=lines.append, stop=threading.Event())
    done = processes.output(argv)
    assert len(_STARTS) == 3
    for kwargs in _STARTS:
        assert kwargs["start_new_session"] == (sys.platform != "win32")
        if sys.platform == "win32":
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
            assert kwargs["creationflags"] & flags == flags
        assert (kwargs["encoding"], kwargs["errors"]) == ("utf-8", "replace")
    for said in (seen, lines, done.stdout.splitlines()):
        pid, group, session = (int(part) for part in said[0].split())
        if sys.platform != "win32":
            assert pid == group == session
        assert said[1] == "é \ufffd"


def test_kill_waits_for_the_processes_it_is_told_of() -> None:
    """The child's own process is waited for, and so is each process it reported: on Windows the
    interpreter behind a venv's launcher. Gone when kill returns, with no wait in the test."""
    starts = (
        "import os, subprocess, sys, time\n"
        "other = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(other.pid, flush=True)\n"
        "time.sleep(60)\n"
    )
    child = Child.start([sys.executable, "-c", starts], env=dict(os.environ))
    other = int(next(child.lines(threading.Event())))
    assert alive(other)
    child.kill(also=[other])
    assert child.returncode is not None
    assert not alive(other)


class _Reaped:
    """A /proc entry whose process was reaped after the signal found it, before it was read."""

    def __init__(self, _path: str) -> None:
        pass

    def read_text(self, encoding: str) -> str:
        raise FileNotFoundError


class _Dying(_Reaped):
    """A /proc entry read while its process is being reaped: dead, its id not yet free."""

    def read_text(self, encoding: str) -> str:
        return "4321 (python) X 1 4321 4321 0 -1"


def test_a_zombie_reaped_while_it_is_asked_about_is_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    """The signal finds a zombie, which is reaped before its state is read: asked again, the signal
    finds nothing, so it is gone. Where the state cannot be read, as without /proc, a process the
    signal still finds is alive; one whose state says it is being reaped is gone. The Linux path is
    taken on every system."""
    monkeypatch.setattr(processes, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(processes, "Path", _Reaped)
    answers: list[ProcessLookupError | None] = [None, ProcessLookupError()]

    def signal(_pid: int, _signal: int) -> None:
        if (answer := answers.pop(0)) is not None:
            raise answer

    monkeypatch.setattr(os, "kill", signal)
    assert not alive(4321)
    monkeypatch.setattr(os, "kill", lambda _pid, _signal: None)
    assert alive(4321)
    monkeypatch.setattr(processes, "Path", _Dying)
    assert not alive(4321)


def test_kill_after_the_end_never_raises() -> None:
    child = Child.start([sys.executable, "-c", "print('done')"], env=dict(os.environ), stdin="pipe")
    assert list(child.lines(threading.Event())) == ["done"]
    assert child.returncode == 0
    child.kill(also=[child.pid])
    child.kill()  # twice, its stdin already closed
    child.close_stdin()
    assert child.returncode == 0


def test_nothing_is_handed_on_after_a_kill() -> None:
    """A process the child left running may hold its stderr open: the end waits for it a bounded
    time, and once the child is killed its callback hears nothing more."""
    leaves = (
        "import subprocess, sys\n"
        "talks = 'import sys, time\\nfor _ in range(100):\\n    print(1, file=sys.stderr, flush=True)\\n"
        "    time.sleep(0.05)\\n'\n"
        "quiet = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL}\n"
        "subprocess.Popen([sys.executable, '-c', talks], **quiet)\n"
    )
    heard: list[str] = []
    child = Child.start([sys.executable, "-c", leaves], env=dict(os.environ), stderr=heard.append)
    assert list(child.lines(threading.Event())) == []
    child.kill()
    count = len(heard)
    time.sleep(0.3)
    assert count > 0 and len(heard) == count
