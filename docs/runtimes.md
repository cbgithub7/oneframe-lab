# Writing a runtime

A runtime is the Python environment a family of heavy nodes runs in: its own Python, its own
torch, its own packages. Families pin conflicting versions of all three, so each gets its own
environment, and a node reaches it only through a child process (see
[architecture.md](architecture.md)).

A runtime is data: a folder in `runtimes/`, named for its id. The engine finds it by looking;
nothing in the engine or the app names one. A node says which runtime it runs in with
`"run": {"where": "runtime", "runtime": "<id>"}` in its `node.json` ([nodes.md](nodes.md)).

## The folder

| File | What |
| --- | --- |
| `pyproject.toml` | A virtual uv project (`[tool.uv] package = false`). Each build is an extra, and all the extras are one set in `[tool.uv] conflicts` |
| `uv.lock` | Committed. `uv lock --project runtimes/<id>` makes it; installing runs exactly what it says |
| `runtime.json` | The builds and what each needs from the machine, the Python, compiled extensions, pinned sources, environment variables |

The test runtime, `engine/tests/runtimes/tiny/`, is a complete small example.

## Builds

A build is one way to install the same packages: `cpu`, `cu126`, `cu130`. Each is an extra that
takes torch from its own index:

```toml
[project.optional-dependencies]
cpu = ["torch==2.14.0"]
cu126 = ["torch==2.14.0"]
cu130 = ["torch==2.14.0"]

[tool.uv]
package = false
conflicts = [[{ extra = "cpu" }, { extra = "cu126" }, { extra = "cu130" }]]

[tool.uv.sources]
torch = [
  { index = "pytorch-cpu", extra = "cpu" },
  { index = "pytorch-cu126", extra = "cu126" },
  { index = "pytorch-cu130", extra = "cu130" },
]

[[tool.uv.index]]
name = "pytorch-cu126"
url = "https://download.pytorch.org/whl/cu126"
explicit = true
```

Because the extras conflict, one `uv.lock` holds every build, each at its own versions: a build
for older cards can stay on the last torch that supports them while another moves on.

In `runtime.json`, `builds` lists them fastest first, each with what it needs:

```json
{ "name": "cu126", "vendor": "nvidia", "min_capability": "5.0", "max_capability": "9.0",
  "min_driver": { "windows": "528.33", "linux": "525.60.13" }, "disk_mb": 6000 }
```

- `vendor`: `nvidia`, `amd`, `intel`, or `none` for the build that runs on the processor.
- `min_capability`, `max_capability`: the compute capabilities its kernels cover. `"12"` as a
  maximum means any 12.x.
- `min_driver`: the oldest driver it runs on, as one version or one per OS.
- `os`: the systems it runs on, if not all.
- `disk_mb`: about how much it takes installed. A plan is blocked when the data root has less.

## The rest of runtime.json

```json
{
  "id": "demo",
  "title": "Demo",
  "summary": "One line: which nodes run here.",
  "python": "3.14",
  "builds": [ ... ],
  "extensions": [
    { "package": "fastmesh", "class": "stand-in", "for": "mesh cleanup", "stand_in": "stand-ins/fastmesh" },
    { "package": "flashattn", "class": "optional", "for": "faster attention", "why": "the nodes fall back to PyTorch attention" },
    { "package": "rasterizer", "class": "required", "for": "texture baking", "needs": "a C++ compiler and the CUDA Toolkit 12.6" }
  ],
  "sources": [
    { "name": "upstream", "url": "https://codeload.github.com/<owner>/<repo>/tar.gz/<commit>",
      "sha256": "<64 hex digits>", "paths": ["."] }
  ],
  "env": { "SOME_FLAG": "1" },
  "probe": "probe.py:run"
}
```

- `python`: the CPython this runtime uses. uv fetches it; a system Python is never used.
- `extensions`: compiled packages, each in one class.
    - `stand-in`: a pure-Python replacement ships in this folder and is put on the path.
    - `optional`: left out; `why` says what the nodes do without it.
    - `required`: needs a compiler. The runtime is blocked on every machine, and the reason names
      the package and `needs`. Prebuilt wheels will be added per runtime when a node needs one.
- `sources`: upstream code that is not on an index, as a pinned archive. Its sha256 is checked,
  every file in it must stay inside its folder, and `paths` inside it are made importable.
- `env`: variables set for every node run in this runtime. It may not set what the engine sets or
  removes for every run: the hub libraries' offline flags, hub tokens and endpoints, torch's
  `weights_only` switches, and what keeps a run under the data root (the library caches, the
  temporary folder, bytecode, and the hub and Triton cache variables that would override them).
- `probe`: a function the runtime report runs inside the runtime.

## Checks

A definition is checked when it is read, and every problem is reported at once next to the
runtimes that are fine:

- the id matches the folder, and the three files are there;
- every build is an extra, and all of them are one conflict set;
- every torch in `uv.lock` is 2.10 or newer. Older torch can run code from a crafted weights file,
  even with `weights_only` (CVE-2025-32434 before 2.6, CVE-2026-24747 before 2.10), so a lock that
  holds one is refused;
- each extension, source and variable is well formed.

## Locking

```
uv lock --project runtimes/<id>
```

Commit `uv.lock` with the change that needed it. A GPU build's index must be reachable when
locking; installing needs only what the lock names.

## On this machine

Nothing is installed until a person asks. The engine picks the build this machine runs (see
[architecture.md](architecture.md)); installing it builds, under the data root:

```
<data>/runtimes/<id>/<build>/                         the environment
<data>/runtimes/<id>/<build>/src/<name>/              the pinned sources
<data>/runtimes/<id>/<build>/stand-ins/               the stand-ins
<data>/runtimes/<id>/<build>/oneframe-runtime.json    the marker, written last
<data>/runtimes/<id>/downloads/                       source archives, by sha256
<data>/uv/cache/, <data>/uv/python/                   uv's cache and the Pythons it fetched
<data>/cache/runtime/<id>/                            the caches its libraries keep (HF_HOME, ...)
<data>/logs/locks/runtime-<id>.lock                   held while it is installed or removed
```

- **The marker** holds its format, the hashes of `uv.lock`, `runtime.json` and the stand-ins the
  environment was built from, the environment's own path, and a freeze of what arrived. Until it
  is written, the runtime is not installed (`not_installed`): an install that stopped, failed or
  was killed is resumed by installing again.
- **Out of date** (`out_of_date`): when `uv.lock`, `runtime.json` or a stand-in changes, the
  runtime is out of date. Nodes refuse to run in it, and installing again rebuilds it; nothing
  rebuilds on its own.
- **Moved** (`moved`): an environment whose marker names another path (a root copied or moved)
  must be built again; installing deletes it first. **Newer** (`newer_format`): one a newer
  version of the app installed is left as it is, neither installed over nor removed.
- **One at a time, across processes** (`locked`): installing or removing a runtime holds its lock,
  so the app and a command-line tool on the same root never build it at once. The operating
  system releases the lock when its holder dies.
- **What a run writes** stays under the root: the child's library caches go to
  `cache/runtime/<id>/`, its temporary files to its job's folder, and it writes no bytecode
  (`executors.child_env`). A node's cache key includes the build and the marker's hashes, so a
  rebuilt runtime never serves results from before.
- **Several cards:** the build is planned for the NVIDIA card with the most memory, and a node's
  child sees only that card (`CUDA_DEVICE_ORDER=PCI_BUS_ID`, `CUDA_VISIBLE_DEVICES`), so its
  `cuda` is the card the build was chosen for. The fit uses that card too (`device_target`), even when
  another has more free; choosing among cards per node is a known limit. A node that runs on the
  processor in a GPU build gets `CUDA_VISIBLE_DEVICES=-1`, so it sees no card.
- **Learning:** what a machine learns about a node's memory is kept under the runtime's build and
  lock hash, so a changed lock starts it over.
- **Removing** a runtime deletes `<data>/runtimes/<id>/` and nothing else.
- **Sizes:** uv links files from its cache into environments on the same volume instead of copying
  them, which is why the cache lives under the data root too. A runtime's size is the size of its
  folder, so files it shares with the cache are counted there and in the cache.

## Versions

A runtime uses the newest Python and torch its packages support ([versions.md](versions.md)). A
build held back, for example for older cards, is written in `versions.json` with the reason.
