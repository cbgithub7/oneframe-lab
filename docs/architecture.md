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
| Scheduler | `oneframe/scheduler.py` | Runs a plan; fits each step to memory; checks every value at every port; carries trust; one retry after `oom` |
| Memory | `oneframe/memory.py` | A node's memory model; the estimate; the margins and the person's settings; the fit (pure); what each machine learns |
| Executors | `oneframe/executors.py`, `oneframe/child.py` | In-process or child-process runs; one `NodeContext` either way |
| Runtimes | `oneframe/runtimes.py`, `oneframe/runtime_install.py` | Find runtime definitions; plan the build a machine runs; install, check and remove it; give the scheduler its interpreter ([runtimes.md](runtimes.md)) |
| Hardware | `oneframe/hardware.py` | The machine profile a plan reads: NVIDIA cards, driver, OS, system memory (total and available), free disk |
| Archives | `oneframe/archives.py` | Pinned downloads kept only when their sha256 matches; unpacking that stays inside its folder |
| Server | `oneframe/server.py` | The engine's NDJSON protocol |
| Benches | `oneframe/bench_runtime.py`, `oneframe/bench_fit.py` | The reports a hardware claim needs: a runtime's install, and a node's memory on a card |
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

**Fit before load.** The machines this runs on range from no GPU to cards of 80 GB, and nobody
should have to run a test to learn what fits. Each node declares a memory model; before it loads,
the engine measures free memory, keeps a margin, and changes only the settings the graph left
alone, speed before quality, labelling the result (`meta.made_with`). A slower device is announced
with its expected time and the faster alternative at reduced quality. The child caps torch's
allocator, so an estimate that is too low becomes an `oom` the engine answers once with a smaller
fit; each machine corrects the estimates from the peaks it measures. Nothing decides on a card's
name.

**A cache keyed by content.** Exploring means changing one thing and looking again. With every
output keyed by what produced it, changing a parameter re-runs only what follows it, and two
recipes that start the same way share their start.

**One door into the page.** The page is sandboxed, has no Node, and can call only the engine
methods `ENGINE_METHODS` lists. A compromised page can ask the engine to run a graph; it cannot
read a file or start a process.

## Events

A run answers `graph.run` at once with a run id; everything after is pushed:

`run.start` → per node: `node.cached` (the result at its own values), or `node.fit` (the device,
precision, changes, needs, budgets and any warning: `slow`, `tried_anyway`, `unknown`) then
`node.cached` (a reduced result the fit allows) or `node.start` (with its `attempt`), then `stage` /
`progress` / `ceiling` / `node.step_oom` from the node, then `node.done` (peaks, `made_with`, the
fit) | `node.oom` and, once, another `node.fit` and `node.start` | `node.failed` (with the fits) →
`run.done` | `run.failed` | `run.stopped`. An engine node without a memory model gets no `node.fit`.

Failure kinds: `oom` (retried once with a strictly smaller fit, in a new process for a runtime
node; not for a node without a memory model, tried anyway, or with the fit off), `memory` (nothing
fits on any device; the message says what would help), `fetch` (something tried to download),
`missing` (an import the runtime lacks), `runtime` (the runtime cannot run yet; `reason` says
whether it is not installed, out of date, being installed, blocked on this machine, or unknown),
`contract` (a node broke its manifest), `node` (the node explained), `error`, `died` (the process
ended without a word; it says when the exit code means the system ran out of memory).

`nodes.fit` answers the fit a node would get now, and `nodes.forget` clears what this machine
learned about a node. Neither is on the page's method list yet.

A runtime install answers `runtimes.install` at once with the build it will install; then:

`runtime.start` → per step `runtime.step` (index and total), with `runtime.log` lines from uv and
`runtime.progress` for source downloads → `runtime.done` | `runtime.failed` | `runtime.stopped`.
`runtimes.stop` ends an install; one install runs at a time.

## What comes next

1. Model store: Download for a node's weights, into the data root.
2. Keep models loaded: a long-lived worker per runtime in place of one child process per run.
3. The first model nodes: two depth models, a segmenter, an object generator, a view synthesiser
   and a reconstructor, enough for both loops, each with a memory model measured with
   `bench:fit`.
4. Workspace UI: variants side by side, a graph editor, the three.js + Spark viewer.
