"""The engine as the app runs it: a process, requests on stdin, answers and events on stdout.

It starts the command it is given, so a test starts the engine as it needs (the module, or
fixtures/serve.py to trust the test hosts' CA; its own environment and folder). Its output is read
on a thread, so every wait ends at its deadline, even while the engine says nothing; a wait that
fails names what it waited for and the log that holds the engine's standard error. Tests get one
from conftest's `engine_process` fixture, which kills it at teardown if it is still running."""

from __future__ import annotations

import contextlib
import json
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

Message = dict[str, Any]


class EngineProcess:
    def __init__(
        self,
        argv: list[str],
        log: Path,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        wait_s: float = 30,
    ):
        """`wait_s`: how long a wait lasts unless it says otherwise."""
        self.log = log
        self.wait_s = wait_s
        self._log = log.open("a", encoding="utf-8")
        self.process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._log,
            env=env,
            cwd=cwd,
            text=True,
            encoding="utf-8",
        )
        self.seen: list[Message] = []  # every message the engine has said, in order
        self._unread: list[Message] = []  # those no wait has taken yet
        self._said = threading.Condition()
        self._ended = False
        self._next_id = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self.process.stdout is not None
        try:
            for line in self.process.stdout:
                if line.strip():
                    message = json.loads(line)
                    with self._said:
                        self.seen.append(message)
                        self._unread.append(message)
                        self._said.notify_all()
        finally:
            with self._said:
                self._ended = True
                self._said.notify_all()

    def wait(self, match: Callable[[Message], bool], what: str, seconds: float | None = None) -> Message:
        """The first message not yet taken that `match` accepts, taken. Past the deadline, or once
        the engine has stopped talking, the test fails naming `what`."""
        limit = self.wait_s if seconds is None else seconds
        deadline = time.monotonic() + limit
        with self._said:
            while True:
                found = next((m for m in self._unread if match(m)), None)
                if found is not None:
                    self._unread.remove(found)
                    return found
                where = f"the engine's log is {self.log}"
                assert not self._ended, f"the engine ended before {what}; {where}"
                left = deadline - time.monotonic()
                assert left > 0, f"waited {limit:g} s for {what}; {where}"
                self._said.wait(left)

    def event(self, *kinds: str, seconds: float | None = None, **fields: Any) -> Message:
        """The next event of one of these kinds whose fields have these values."""
        return self.wait(
            lambda m: m.get("event") in kinds and all(m.get(k) == v for k, v in fields.items()),
            "/".join(kinds),
            seconds,
        )

    def call(self, method: str, params: dict[str, Any] | None = None) -> Message:
        """Send a request and wait for its answer; events said meanwhile stay to be waited for."""
        assert self.process.stdin is not None
        self._next_id += 1
        ident = self._next_id
        self.process.stdin.write(json.dumps({"id": ident, "method": method, "params": params or {}}) + "\n")
        self.process.stdin.flush()
        return self.wait(lambda m: m.get("id") == ident, method)

    def close(self, seconds: float = 60) -> int:
        """Close its stdin, as the app does when it quits, and wait for it to end; its exit code."""
        assert self.process.stdin is not None
        if not self.process.stdin.closed:
            self.process.stdin.close()
        code = self.process.wait(timeout=seconds)
        self._end()
        return code

    def kill(self) -> None:
        """End it at once, as a crash would, if it is still running."""
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=30)
        self._end()

    def _end(self) -> None:
        # stdout is the reader's to finish: a child the engine left may still hold it open.
        stdin = self.process.stdin
        if stdin is not None and not stdin.closed:
            with contextlib.suppress(OSError):  # the engine is gone; nothing waits to be sent
                stdin.close()
        self._log.close()
