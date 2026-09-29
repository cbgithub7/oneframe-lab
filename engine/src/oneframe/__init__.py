"""Oneframe Lab's engine.

Everything a pipeline is made of is data: node manifests (`nodes/*/node.json`), the port types
(`contracts/port-types.json`) and recipes (graphs a person builds or saves). This package reads
that data, checks a graph against it, and runs it: in the engine's own process for light nodes,
in a child process in a node's own runtime for the heavy ones, with every output cached by
content so that changing one node re-runs only what comes after it.

Importing this package must stay cheap. No model library is imported here or by anything this
package imports at module level; node code is loaded only when a node runs.
"""

from __future__ import annotations

import os
from pathlib import Path

__version__ = "0.1.0"

ENGINE_DIR = Path(__file__).resolve().parent
# The folder that holds contracts/ and nodes/: the repo in development, the unpacked app
# resources when packaged (the app sets ONEFRAME_ROOT then).
REPO_ROOT = Path(os.environ.get("ONEFRAME_ROOT") or ENGINE_DIR.parents[2])
CONTRACTS_DIR = REPO_ROOT / "contracts"
BUILTIN_NODES_DIR = REPO_ROOT / "nodes"
