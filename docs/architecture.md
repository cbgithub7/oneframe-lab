# Architecture

Oneframe Lab turns one photo into 3D content through graphs of interchangeable nodes. The
proposal behind this design, with the research on each model family, is the "Oneframe Lab:
foundation proposal" doc; this file is the part that stays true in the code.

```
Electron main ── engine:request / engine:event ── renderer (sandboxed page)
      │
      │ NDJSON over stdin/stdout
      ▼
Engine (Python 3.15, uv): registry · graph planner · scheduler · cache
      │
      ├── engine nodes: run in the engine process (sources, converters, evaluators)
      └── runtime nodes: one child process per run, in the node's own uv environment,
                         JSON job in, NDJSON events out, network closed, its processes ended on Stop
```

## The parts

| Part | File | Job |
| --- | --- | --- |
| Port types | `contracts/port-types.json`, `oneframe/ports.py` | The data kinds, their facets, and the rule for connecting them |
| Manifests | `oneframe/manifest.py`, `oneframe/reading.py` | Read and check `node.json` part by part; every problem reported at once, a field of the wrong type among them, never a crash. `reading.py`, standard library only, is the one way a definition is read: a manifest, its memory model and a runtime's (the text field, the id rule, the version compare, a JSON object from a file, `ProblemsError`, `discover`) |
| Registry | `oneframe/registry.py` | Find nodes by looking in folders, through the `discover` runtimes use too; a broken node never hides the others, and is listed as a problem |
| Graph | `oneframe/graph.py` | Recipes (graphs as JSON); `plan()` checks them, inserts converters, orders them |
| Cache | `oneframe/cache.py` | Outputs stored by a hash of node, version, params, input keys, the node's code and its runtime's build; atomic writes |
| Scheduler | `oneframe/scheduler.py` | Runs a plan; fits each step to memory; asks the `Executors` it is given where each attempt runs; checks every value at every port; carries trust; one retry after `oom` |
| Memory | `oneframe/memory/` | A node's memory model, which a manifest reads alone of the five (`model.py`); the person's settings and the margins (`settings.py`); the estimate (`estimate.py`); the fit, pure (`fit.py`); what each machine learns (`learned.py`) |
| Errors | `oneframe/errors.py` | Every kind and reason of failure, declared once, and whose fault each is ([Failures](#failures)) |
| Executors | `oneframe/executors.py`, `oneframe/child.py`, `oneframe/node/` | In-process or child-process runs behind one `Executor` Protocol; `OneProcessPerRun`, the engine's `Executors`, starts a child per attempt and hides the cards from a processor run; `run_probe` runs a runtime's own code for the benches; one node API and one runner either way, and the child a bootstrap that makes only `oneframe.node` importable |
| Processes | `oneframe/processes.py` | The one module that starts, reads and ends processes: each in its own group, with no window, read as UTF-8; a kill ends the tree and waits for it |
| Environment | `oneframe/environment.py`, `app/main/paths.js` | What every child inherits (never the engine's own Python or a hub token) and what the engine sets: a run offline, its writes under the root; uv's folders under the root |
| Runtimes | `oneframe/runtimes/` | Find runtime definitions (`definition.py`); plan the build a machine runs (`plan.py`, which loads neither the manager nor the install); install it (`install.py`); check and remove it, and give the executors its interpreter, variables and cache folder, and the scheduler its fit's target and cache key (`manager.py`) ([runtimes.md](runtimes.md)) |
| Hardware | `oneframe/hardware.py` | The machine profile a plan reads: NVIDIA cards, driver, OS, system memory (total and available), free disk |
| Model store | `oneframe/models/` | The files nodes read ([spec 003](../specs/003-model-store/spec.md)): declared in manifests (`schema.py`), kept once each by sha256 under `models/` (`store.py`), fetched on `http.client` by a transport that heals itself (`transport.py`, which fetches a runtime's sources too), as download and verify jobs (`jobs.py`), checked before a run (`runs.py`), listed and removed (`catalog.py`), pinned (`pin.py`); the two command-line tools (`tools.py`) |
| Archives | `oneframe/archives.py` | Unpacking a runtime's pinned source, which stays inside its folder; the source is fetched by the model store's transport (`fetch_source` in `oneframe/runtimes/install.py`) |
| Layout | `oneframe/layout.py`, `app/main/paths.js`, `contracts/layout.json` | The data root, defined once, and every path under it; the root file and its format |
| Files | `oneframe/files.py` | Writing a file in one step (`write_text`), the one JSON writer, which never replaces a newer format; operating-system locks; the one file hash (`sha256_file`), move into place (`replace`, which waits on Windows while another program holds the target) and free space (`free_bytes`) |
| Scratch | `oneframe/scratch.py` | Each process's locked folder under `cache/tmp/`; a start removes only dead processes' folders |
| Journal | `oneframe/journal.py` | Every event of a run, an install or a model job, kept in a journal; journals and step logs pruned when one opens; one helper ends each after its place is free |
| Diagnose | `oneframe/diagnose.py` | `npm run diagnose`: one file to hand over, with the user's name out of every path |
| Server | `oneframe/server.py` | The engine's NDJSON protocol |
| Benches | `oneframe/bench_runtime.py`, `oneframe/bench_fit.py`, `engine/tests/fit_check.py` | The reports a hardware claim needs: a runtime's install, a node's memory on a card, and spec 002's check on a card, which lives with its test node |
| Tool launcher | `scripts/tool.js` | Starts each command-line tool that opens a data root, with uv's temporary files under that root |
| Engine client | `app/main/engine.js` | The app's side of the protocol |
| Main | `app/main/main.js`, `app/main/boot.js`, `app/main/door.js` | Electron's folders under the root, the window, the engine process, and the one IPC door, which answers with values |

## Why it is shaped this way

**A playground for graphs.** The product is an editor where a person builds graphs from nodes and
compares what they make. A graph that ships with the app is only a starting point, saved as data;
the engine and the page work on whatever graph the person builds, and depend on none. General node
editors exist (ComfyUI, for one), so this one earns its place by what its graphs mean, not by the
editor itself (the [review of 2026-10-12](reviews/2026-10-12.md), decision 1):

- every model family in its own environment, behind a process boundary, so families that pin
  conflicting libraries never meet;
- each node fitted to the memory this machine has free, on any machine from no GPU to the largest
  cards;
- typed geometry: facets on every port, so disparity never becomes metric depth without a
  converter, and a trust label on every result;
- no network during a run.

**Nodes are data.** Every earlier attempt to support "one more model" by adding a branch to the
engine ended with an engine that knew every model by name. Here, the engine knows port types and
manifests, nothing else.

**Facets on ports.** "Depth" from Depth Pro (metric z), Depth Anything (relative disparity) and
UniK3D (metric range) are three different things. Making the difference part of the type means
the planner refuses a wrong edge, or inserts a converter, instead of producing wrong geometry
without an error.

**A child process per heavy node.** Model families pin conflicting versions of torch, CUDA
extensions and numpy. One environment per family, reached through a process boundary, means they
never meet. The same boundary is what makes Stop immediate (the child and every process it started
are killed), and what lets the engine close the network to Python code and cap the GPU allocator
before any model code runs. The closed network guards against accidents (a library fetching
weights nobody asked for), not against malicious code: native code and DNS lookups go around it.

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
lock what they work on, and the operating system releases a lock when its holder dies (on Windows,
a moment after).

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

A model job, `models.download {node, checkpoints?}` or `models.verify {node?}`, answers at once with
its job id, which every event carries ([spec 003](../specs/003-model-store/spec.md)):

`model.start` → per file `model.file` (its phase: `checking`, `downloading`, `retrying`,
`verifying`, `placing`) and throttled `model.progress` → `model.done` | `model.failed` (the first
failed file's kind and reason) | `model.stopped`, each listing every file's outcome. `downloading`,
`retrying` and a failed file name the hosts that attempt asked, in order: names and ports, never a
path or a query, since a signed URL's are a credential. `models.stop`
ends a job within 2 s and keeps its partial; one job runs at a time across processes.
`models.list` answers what the store holds for each node, by shared files and by checkpoint, and
what no node pins, without the network; `models.remove {node, checkpoint?}` or
`{unpinned: [...]}` deletes only that, keeping a file another node pins, and is refused while the
registry has problems, a folder of nodes is missing, a job runs, or a running graph may read the
file. None of these is on the page's method list yet.

**An ending is sent after its place is free** (`journal.end_when_free`, spec 010). A run lets go of
its list of the files it reads and of the engine's run slot, an install of its slot and its
runtime's lock, and a job of the store's lock, each after its ending is in the journal and before it
is sent, an engine crash's ending too. So a page that acts on hearing one (a next run, the same
install again, removing the run's runtime or a file it read) is answered, not refused `busy` or
`in_use`. A next run's or job's first events may then come before the last one's ending, told apart
by `run` or `job`; a next install's never do, since an install lets go and sends under its claim
lock.

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
| `files` |  | no | A file the node reads is not downloaded and checked; no process started. |
| `files` | `not_downloaded` | no | A file it reads has not been downloaded. |
| `files` | `downloading` | yes | A file it reads is being downloaded now. |
| `files` | `incomplete` | no | A download of a file it reads stopped partway; downloading again resumes it. |
| `files` | `changed` | no | A file it reads changed since it was checked, or a newer app recorded it. |
| `download` |  | no | A file could not be downloaded; nothing but its checked bytes was kept. |
| `download` | `source_changed` | no | The source gives another size than the pin; it changed since. |
| `download` | `hash_mismatch` | yes | The bytes fetched are not the pinned file; nothing was kept. |
| `download` | `no_space` | no | The store's drive is too full; the partial download was kept. |
| `download` | `stalled` | yes | Attempt after attempt fetched nothing; the partial download was kept. |
| `download` | `tls` | no | The connection could not be secured: a certificate refused, or http. |
| `download` | `rate_limited` | yes | The host asked to wait longer than a download waits. |
| `download` | `gated` | no | The file is gated: its licence must be accepted on its page first. |
| `download` | `not_found` | no | The source has no such repository or file. |
| `download` | `revision_gone` | no | The pinned commit is gone from its repository. |
| `store` |  | no | The model store refused a download, verify, remove or pin. |
| `store` | `newer_format` | no | A newer version of the app wrote it; it is left as it is. |
| `store` | `unreadable` | no | Its format file cannot be read, or does not say its format. |
| `store` | `busy` | yes | A download or verify is running in this engine; they run one at a time. |
| `store` | `locked` | yes | Another process is downloading, verifying or removing model files. |
| `store` | `in_use` | yes | A graph that is running may read it; nothing was deleted. |
| `store` | `registry_problems` | no | A node's manifest cannot be read, so what it pins is unknown. |
| `store` | `node_root_missing` | no | A folder of nodes is missing, so what its nodes pin is unknown. |
| `store` | `os_refused` | yes | The system refused to rename or delete a file; it and its record were kept. |
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

The order of work, and what is in its way, is in [handoff.md](handoff.md); what is parked is in
[roadmap.md](roadmap.md).
