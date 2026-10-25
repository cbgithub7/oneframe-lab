"""Starting, reading and ending the processes the engine runs: the one module that does.

Every process gets its own session (POSIX) or process group (Windows) and no console window, and
its output is read as UTF-8, undecodable bytes replaced, whatever the locale says. Three ways in:

- `Child`, a process the caller reads line by line until its end, a Stop, or a kill. Killing one
  ends its whole tree and waits, a bounded time, for it and for any process the caller names.
- `run`, a command run to its end or a Stop, its output passed on line by line.
- `output`, a short command's output, both streams apart.

Ending a tree: on POSIX, the child's group is killed, only while the child is unreaped, so its id
cannot have been reused; a process that starts a session of its own has left the group and is
missed. On Windows, System32's `taskkill /T /F` ends the child and every process it started. Only
a kill ends a tree: what a process leaves running after a normal end stays.

Standard library and `Stopped` only.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator, Mapping
from pathlib import Path
from subprocess import CompletedProcess, TimeoutExpired
from types import TracebackType
from typing import IO, Literal

from oneframe.node import Stopped

__all__ = ["Child", "CompletedProcess", "TimeoutExpired", "alive", "output", "run", "wait_gone"]

LOGGER = logging.getLogger(__name__)
POLL_S = 0.2  # how often a read checks for Stop
KILL_WAIT_S = 10.0  # how long a kill waits for the processes it ends, all of them together
DRAIN_S = 2.0  # how long, after an end, the rest of a separate stderr is waited for
GROUP = {
    "start_new_session": sys.platform != "win32",
    "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)
    | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
}

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _KERNEL32.OpenProcess.restype = wintypes.HANDLE
    _KERNEL32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    _KERNEL32.WaitForSingleObject.restype = wintypes.DWORD
    _KERNEL32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    _KERNEL32.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    _KERNEL32.CloseHandle.argtypes = (wintypes.HANDLE,)
    _SYNCHRONIZE = 0x00100000
    _QUERY = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION
    _STILL_ACTIVE = 259
    _TASKKILL = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"


def alive(pid: int) -> bool:
    """Whether a process is still running. One that has ended but is not yet reaped counts as gone;
    on Windows this asks without touching the process."""
    if sys.platform == "win32":
        handle = _KERNEL32.OpenProcess(_QUERY, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            _KERNEL32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == _STILL_ACTIVE
        finally:
            _KERNEL32.CloseHandle(handle)
    if not _found(pid):
        return False
    try:
        state = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0]
    except OSError, IndexError:  # no /proc, or a zombie reaped since it was found: ask again
        return _found(pid)
    return state != "Z"


def _found(pid: int) -> bool:
    """Whether a signal finds the process (POSIX), a zombie included."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # found, but not ours to signal
        pass
    return True


def wait_gone(pid: int, seconds: float = 10) -> bool:
    """True once the process is gone, False if it is still running after `seconds`."""
    end = time.monotonic() + seconds
    while alive(pid):
        if time.monotonic() > end:
            return False
        time.sleep(0.05)
    return True


def _watch(pid: int) -> Callable[[float], bool]:
    """A wait for `pid` to be gone, made before the kill. On Windows it holds a handle to the
    process, so no other process can take the id while it waits."""
    if sys.platform == "win32":
        handle = _KERNEL32.OpenProcess(_SYNCHRONIZE, False, pid)
        if not handle:
            return lambda _seconds: True  # already gone

        def wait(seconds: float) -> bool:
            try:
                return _KERNEL32.WaitForSingleObject(handle, round(seconds * 1000)) == 0
            finally:
                _KERNEL32.CloseHandle(handle)

        return wait
    return lambda seconds: wait_gone(pid, seconds)


def _kill_tree(pid: int, seconds: float) -> None:
    """End `pid` and every process it started; whatever fails, the caller kills `pid` itself."""
    if sys.platform == "win32":
        with contextlib.suppress(OSError, TimeoutExpired):
            output([str(_TASKKILL), "/T", "/F", "/PID", str(pid)], timeout=seconds)
    else:
        with contextlib.suppress(OSError):
            os.killpg(pid, signal.SIGKILL)


def _read(stream: IO[str], put: Callable[[str], None], end: Callable[[], None] = lambda: None) -> None:
    try:
        for line in iter(stream.readline, ""):
            put(line.rstrip("\r\n"))
    except OSError, ValueError:  # the stream closed under the reader
        pass
    finally:
        end()


class Child:
    """A process this engine started, in its own group, read line by line. Used as a context
    manager, it is killed when the block ends, however it ends. Once it is killed, nothing more
    is handed to its stderr callback, whatever still writes to that stream."""

    def __init__(self, proc: subprocess.Popen[str], stderr: Callable[[str], None] | None):
        self._proc = proc
        self.pid = proc.pid
        self._lines: queue.Queue[str | None] = queue.Queue()
        self._stderr = stderr
        self._handing = threading.Lock()  # held while a line is handed to `stderr`
        self._killed = False
        self._drained = False
        assert proc.stdout is not None
        self._readers = [
            threading.Thread(
                target=_read, args=(proc.stdout, self._lines.put, lambda: self._lines.put(None)), daemon=True
            )
        ]
        if stderr is not None:
            assert proc.stderr is not None
            self._readers.append(threading.Thread(target=_read, args=(proc.stderr, self._hand), daemon=True))
        for reader in self._readers:
            reader.start()

    @classmethod
    def start(
        cls,
        argv: list[str],
        *,
        env: Mapping[str, str],
        cwd: Path | None = None,
        stdin: Literal["null", "pipe"] = "null",
        stderr: Literal["merge"] | Callable[[str], None] = "merge",
    ) -> Child:
        """Start `argv`. Its stdin is empty, or a pipe this process holds open (`close_stdin`); its
        stderr is merged into the lines, or each line of it is handed to `stderr`, on a thread of
        its own. Raises OSError when it cannot start."""
        proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE if stdin == "pipe" else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT if stderr == "merge" else subprocess.PIPE,
            env=dict(env),
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            **GROUP,
        )
        return cls(proc, None if stderr == "merge" else stderr)

    @property
    def returncode(self) -> int | None:
        """The exit code, once the child has ended and been waited for."""
        return self._proc.returncode

    def lines(self, stop: threading.Event) -> Iterator[str]:
        """Each line the child writes, without its line end, then its exit. Raises Stopped once
        `stop` is set, even after the output has ended: a child that closed it may still run."""
        while True:
            if stop.is_set():
                raise Stopped()
            try:
                line = self._lines.get(timeout=POLL_S)
            except queue.Empty:
                continue
            if line is None:
                break
            yield line
        while self.wait(POLL_S) is None:
            if stop.is_set():
                raise Stopped()
        self._drain()

    def close_stdin(self) -> None:
        if self._proc.stdin is not None:
            with contextlib.suppress(OSError):
                self._proc.stdin.close()

    def wait(self, seconds: float | None = None) -> int | None:
        """The exit code, or None when the child is still running after `seconds`."""
        try:
            return self._proc.wait(seconds)
        except TimeoutExpired:
            return None

    def kill(self, also: Iterable[int] = ()) -> None:
        """End the child's tree, then the child, and close its stdin, taking at most KILL_WAIT_S
        in all to end and wait for the child and each process in `also`: one the child reported,
        such as the interpreter behind a venv's launcher. Then its last output, and no more. Never
        raises; what does not end in time is logged."""
        deadline = time.monotonic() + KILL_WAIT_S

        def left() -> float:
            return max(deadline - time.monotonic(), 0)

        watches = {pid: _watch(pid) for pid in also}
        if self._proc.returncode is None:
            _kill_tree(self.pid, left())
        with contextlib.suppress(OSError):
            self._proc.kill()
        self.close_stdin()
        if self.wait(left()) is None:
            LOGGER.warning("process %s did not end within %s s of being killed", self.pid, KILL_WAIT_S)
        for pid, gone in watches.items():
            if not gone(left()):
                LOGGER.warning("process %s did not end within %s s of a kill", pid, KILL_WAIT_S)
        self._drain()
        with self._handing:
            self._killed = True

    def _hand(self, line: str) -> None:
        with self._handing:
            if not self._killed and self._stderr is not None:
                self._stderr(line)

    def _drain(self) -> None:
        """Let the readers pass on what the child wrote last, waiting DRAIN_S at most, once:
        something else may hold its output open."""
        if self._drained:
            return
        self._drained = True
        deadline = time.monotonic() + DRAIN_S
        for reader in self._readers:
            reader.join(max(deadline - time.monotonic(), 0))

    def __enter__(self) -> Child:
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, trace: TracebackType | None
    ) -> None:
        self.kill()


def run(
    argv: list[str],
    *,
    env: Mapping[str, str],
    on_line: Callable[[str], None],
    stop: threading.Event,
    cwd: Path | None = None,
) -> tuple[int, list[str]]:
    """Run a command to its end, or until `stop` is set (Stopped, its tree ended). Each line of its
    output, stderr merged, goes to `on_line`; the last lines come back with the exit code, for the
    message when it fails."""
    tail: deque[str] = deque(maxlen=40)
    with Child.start(argv, env=env, cwd=cwd) as child:
        for line in child.lines(stop):
            text = line.rstrip()
            if text:
                tail.append(text)
                on_line(text)
    code = child.returncode
    assert code is not None  # lines() ends only once the child has
    return code, list(tail)


def output(
    argv: list[str], *, env: Mapping[str, str] | None = None, timeout: float | None = None
) -> CompletedProcess[str]:
    """Run a short command to its end, with an empty stdin, and keep what it wrote on each stream.
    Raises OSError when it cannot start, and TimeoutExpired, the command killed, after `timeout`."""
    return subprocess.run(
        argv,
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=None if env is None else dict(env),
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **GROUP,
    )
