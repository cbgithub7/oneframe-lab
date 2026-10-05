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
| Cache | `oneframe/cache.py` | Outputs stored by a hash of node, version, params, input keys, the node's code and its runtime's build; atomic writes |
| Scheduler | `oneframe/scheduler.py` | Runs a plan; fits each step to memory; checks every value at every port; carries trust; one retry after `oom` |
| Memory | `oneframe/memory.py` | A node's memory model; the estimate; the margins and the person's settings; the fit (pure); what each machine learns |
| Errors | `oneframe/errors.py` | Every kind and reason of failure, declared once, and whose fault each is ([Failures](#failures)) |
| Executors | `oneframe/executors.py`, `oneframe/child.py` | In-process or child-process runs; one `NodeContext` either way |
| Runtimes | `oneframe/runtimes.py`, `oneframe/runtime_install.py` | Find runtime definitions; plan the build a machine runs; install, check and remove it; give the scheduler its interpreter ([runtimes.md](runtimes.md)) |
| Hardware | `oneframe/hardware.py` | The machine profile a plan reads: NVIDIA cards, driver, OS, system memory (total and available), free disk |
| Archives | `oneframe/archives.py` | Pinned downloads kept only when their sha256 matches; unpacking that stays inside its folder |
| Layout | `oneframe/layout.py`, `app/main/paths.js`, `contracts/layout.json` | The data root, defined once, and every path under it; the root file and its format |
| Files | `oneframe/files.py` | The one JSON writer, which never replaces a newer format; operating-system locks |
| Scratch | `oneframe/scratch.py` | Each process's locked folder under `cache/tmp/`; a start removes only dead processes' folders |
| Journal | `oneframe/journal.py` | Every event of a run or an install, kept in a journal; journals and step logs pruned |
| Diagnose | `oneframe/diagnose.py` | `npm run diagnose`: one file to hand over, with the user's name out of every path |
| Server | `oneframe/server.py` | The engine's NDJSON protocol |
| Benches | `oneframe/bench_runtime.py`, `oneframe/bench_fit.py` | The reports a hardware claim needs: a runtime's install, and a node's memory on a card |
| Engine client | `app/main/engine.js` | The app's side of the protocol |
| Main | `app/main/main.js`, `app/main/boot.js`, `app/main/door.js` | Electron's folders under the root, the window, the engine process, and the one IPC door, which answers with values |

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
engine close the network to Python code and cap the GPU allocator before any model code runs. The
closed network guards against accidents (a library fetching weights nobody asked for), not against
malicious code: native code and DNS lookups go around it.

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

**One data root.** Everything the app, the engine and a runtime child write lies under one folder a
person can see and remove, library caches, temporary files, bytecode and Electron's folders
included, but for the exceptions AGENTS.md names. A footprint test proves the engine's side by
pointing every home and temporary folder into a sandbox; Electron's folders rest on a unit test of
`app/main/boot.js` and a headless run. The open gaps are listed in the handoff.
Every kept file says its format, and an older app leaves a newer one alone. A format goes up only
when an older app would misread or damage the file, and the change that raises it brings the
migration; a new file or folder is not a format change. Processes on one root
lock what they work on, and the operating system releases a lock when its holder dies.

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

`nodes.fit` answers the fit a node would get now, and `nodes.forget` clears what this machine
learned about a node. Neither is on the page's method list yet.

A runtime install answers `runtimes.install` at once with the build it will install; then:

`runtime.start` → per step `runtime.step` (index and total), with `runtime.log` lines from uv and
`runtime.progress` for source downloads → `runtime.done` | `runtime.failed` | `runtime.stopped`.
`runtimes.stop` ends an install; one install runs at a time.

## Failures

Every failure the engine reports (`node.failed`, `run.failed`, `runtime.failed`, `engine.failed`,
and an error reply) carries a `kind`, a snake_case `reason` where it has one, a `message`, `next`
(what to do) where that is known, and `retry`: whether the same request may succeed later
unchanged. `oneframe/errors.py` declares them, and nothing undeclared can be raised; this table is
that declaration, and a test holds the two equal. The app's own failures (`request`, `app`) come
from `app/main/failures.js`, held to this table too, and the page receives every one as a value.

| Kind | Reason | Retry | Meaning |
| --- | --- | --- | --- |
| `oom` |  | yes | The node ran out of memory on its device, after one retry with a smaller fit. |
| `memory` |  | yes | Nothing fits on any device now; the message says what would help. |
| `fetch` |  | no | The node tried to download something; a run reads only what Download fetched. |
| `missing` |  | no | The node imports something its runtime does not have. |
| `node` |  | no | The node failed, and said why. |
| `error` |  | no | The node's code raised an error. |
| `died` |  | no | The node's process ended without a word. |
| `contract` |  | no | A node broke its manifest: an output it did not declare, or one outside its folder. |
| `edge` |  | no | A value reached a port whose facets it does not fit; reported against the edge. |
| `runtime` |  | no | A runtime cannot run, or cannot be installed or removed, now. |
| `runtime` | `not_installed` | no | It is not installed. |
| `runtime` | `out_of_date` | no | Its lock or definition changed since it was installed. |
| `runtime` | `installing` | yes | It is being installed. |
| `runtime` | `blocked` | no | None of its builds can run on this machine. |
| `runtime` | `unknown` | no | No runtime by that name is defined. |
| `runtime` | `moved` | no | It was built in another data root and moved here. |
| `runtime` | `newer_format` | no | A newer version of the app installed it; it is left as it is. |
| `runtime` | `locked` | yes | Another process is installing or removing it. |
| `runtime` | `busy` | yes | Another runtime is being installed; they install one at a time. |
| `runtime` | `in_use` | yes | A run is using it. |
| `runtime` | `no_uv` | no | uv was not found. |
| `runtime` | `wrong_build` | no | Only the build this machine runs is installed. |
| `runtime` | `unsafe_path` | no | Its folder is not a runtime folder in the data root; nothing was deleted. |
| `runtime` | `install_failed` | yes | A step of the install failed; installing again resumes it. |
| `graph` |  | no | The graph cannot run; `problems` says why. |
| `graph` | `busy` | yes | Another run is going; runs go one at a time. |
| `root` |  | no | The engine cannot use this data root; nothing under it was changed. |
| `root` | `newer_layout` | no | A newer version of the app laid it out. |
| `root` | `unreadable` | no | Its root file cannot be read, or does not say its format. |
| `request` |  | no | The request was refused before any work began. |
| `request` | `unknown_method` | no | The engine has no such method. |
| `request` | `bad_params` | no | A parameter is missing, or names something that does not exist. |
| `request` | `refused` | no | The app refused it: an unknown page, or a method or params not allowed. |
| `request` | `not_running` | yes | The engine is not running, or stopped while answering. |
| `request` | `timed_out` | yes | The engine did not answer in time. |
| `app` |  | no | The app could not start the engine. |
| `app` | `no_uv` | no | uv was not found. |
| `app` | `start_failed` | no | The engine exited before it was ready. |
| `engine` |  | no | A bug in the engine itself, with its trace; never blamed on a node. |

`oom` is retried once with a strictly smaller fit, in a new process for a runtime node; not for a
node without a memory model, tried anyway, or with the fit off. `died` says when the exit code means
the system ran out of memory. A value whose facets do not fit the port it reaches is no node's
fault: there is no `node.failed`, and `run.failed` names the `edge` (`from` and `to`). A bug in the
engine itself is never blamed on a node: kind `engine`, with its trace in `detail`.

## What comes next

1. Model store: Download for a node's weights, into the data root.
2. Keep models loaded: a long-lived worker per runtime in place of one child process per run.
3. The first model nodes: two depth models, a segmenter, an object generator, a view synthesiser
   and a reconstructor, enough for both loops, each with a memory model measured with
   `bench:fit`.
4. Workspace UI: variants side by side, a graph editor, the three.js + Spark viewer.
