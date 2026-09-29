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
- `env`: variables set for every node run in this runtime.
- `probe`: a function the runtime report runs inside the runtime.

## Checks

A definition is checked when it is read, and every problem is reported at once next to the
runtimes that are fine:

- the id matches the folder, and the three files are there;
- every build is an extra, and all of them are one conflict set;
- every torch in `uv.lock` is 2.6 or newer. Older torch can run code from a crafted weights file
  (CVE-2025-32434), so a lock that holds one is refused;
- each extension, source and variable is well formed.

## Versions

A runtime uses the newest Python and torch its packages support ([versions.md](versions.md)). A
build held back, for example for older cards, is written in `versions.json` with the reason.
