"""The engine as a process, as `python -m oneframe.server` starts it, but trusting the test CA for
downloads (spec 011): truststore reads only the system's store on Windows, so the engine is handed
the CA as a TLS context, as an engine made in a test's own process is, and no proxy."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # engine/tests, for fixtures.hosts

from fixtures.hosts import client_context

from oneframe import server

if __name__ == "__main__":
    raise SystemExit(
        server.main(sys.argv[1:], transfer={"context": client_context, "proxy": lambda _host: None})
    )
