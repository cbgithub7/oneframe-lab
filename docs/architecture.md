# Architecture

Oneframe Lab turns one photo into 3D content through graphs of interchangeable nodes. The
proposal behind this design, with the research on each model family, is the "Oneframe Lab:
foundation proposal" doc; this file is the part that stays true in the code.

```
Electron main ── engine:request / engine:event ── renderer (sandboxed page)
      │
      │ NDJSON over stdin/stdout
      ▼
Engine (Python 3.14, uv): registry · graph planner · scheduler · cache
      │
      ├── engine nodes: run in the engine process (sources, converters, evaluators)
      └── runtime nodes: one child process per run, in the node's own uv environment,
                         JSON job in, NDJSON events out, network closed, killed on Stop
```

## The parts

| Part | File | Job |
| --- | --- | --- |
| Port types | `contracts/port-types.json`, `oneframe/ports.py` | The data kinds, their facets, and the rule for connecting them |
| Manifests | `oneframe/manifest.py` | Read and check `node.json`; every problem reported at once |
| Registry | `oneframe/registry.py` | Find nodes by looking in folders; a broken node never hides the others |
| Graph | `oneframe/graph.py` | Recipes (graphs as JSON); `plan()` checks them, inserts converters, orders them |
| Cache | `oneframe/cache.py` | Outputs stored by a hash of node, version, params and input keys; atomic writes |
| Scheduler | `oneframe/scheduler.py` | Runs a plan; checks every value at every port; carries trust |
| Executors | `oneframe/executors.py`, `oneframe/child.py` | In-process or child-process runs; one `NodeContext` either way |
| Server | `oneframe/server.py` | The engine's NDJSON protocol |
| Engine client | `app/main/engine.js` | The app's side of the protocol |
| Main | `app/main/main.js` | The window, the engine process, and the one IPC door |

## Why it is shaped this way

**Nodes are data.** Every earlier attempt to support "one more model" by adding a branch to the
engine ended with an engine that knew every model by name. Here, the engine knows port types and
manifests, nothing else.

**Facets on ports.** "Depth" from Depth Pro (metric z), Depth Anything (relative disparity) and
UniK3D (metric range) are three different things. Making the difference part of the type means
the planner refuses a wrong edge, or inserts a converter, instead of producing wrong geometry
without an error.

**A child process per heavy node.** Model families pin conflicting versions of torch, CUDA
extensions and numpy. One environment per family, reached through a process boundary, means they
never meet. The same boundary is what makes Stop immediate (the child is killed), and what lets the
engine close the network and cap the GPU allocator before any model code runs.

**A cache keyed by content.** Exploring means changing one thing and looking again. With every
output keyed by what produced it, changing a parameter re-runs only what follows it, and two
recipes that start the same way share their start.

**One door into the page.** The page is sandboxed, has no Node, and can call only the engine
methods `ENGINE_METHODS` lists. A compromised page can ask the engine to run a graph; it cannot
read a file or start a process.

## Events

A run answers `graph.run` at once with a run id; everything after is pushed:

`run.start` → per node `node.start` | `node.cached`, then `stage` / `progress` / `ceiling` from the
node, then `node.done` | `node.failed` → `run.done` | `run.failed` | `run.stopped`.

Failure kinds: `oom` (the scheduler will try the node's next arrangement, once arrangements land),
`fetch` (something tried to download), `missing` (an import the runtime lacks), `runtime` (not
installed), `contract` (a node broke its manifest), `node` (the node explained), `error`, `died`.

## What comes next

1. Runtime manager: uv environments per node family from lock files, torch chosen by compute
   capability and driver.
2. Model store: Download for a node's weights, into the data root.
3. The first model nodes: two depth models, a segmenter, an object generator, a view synthesiser
   and a reconstructor, enough for both loops.
4. Workspace UI: variants side by side, a graph editor, the three.js + Spark viewer.
