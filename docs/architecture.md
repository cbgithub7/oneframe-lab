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

## Words used here

| Word | Means |
| --- | --- |
| node | One step of a graph, a folder with `node.json` and its code: an *engine node* runs in the engine's process, a *runtime node* in a child process in its runtime |
| manifest | A node's `node.json`: its ports, params, where it runs, the files it reads, its memory model |
| port, type, facet | A node's input or output; its data kind (`Depth`); and the facets that say what the values mean (`kind=metric`). Two ports connect when the output's facets fit the input's |
| converter | A `convert` node of one input and one output that the plan inserts between two ports that differ only in facets |
| trust | What a value rests on: `measured` (seen in the photo), `predicted` (a model's guess) or `synthetic` (made from generated views) |
| graph, recipe | Which nodes, with which params, connected how: JSON a person builds and saves |
| plan | A graph checked and put in order, converters inserted |
| runtime, build | A model family's own Python environment (`runtimes/<id>/`), and one way to install it: `cu130`, `cu126` or `cpu` |
| stand-in | A small pure-Python package a runtime ships in place of a compiled one it cannot install |
| fit | The settings, device and precision a node gets before it loads, so it fits the memory this machine has free |
| budget, margin, cap | The memory a fit allows a node; what it keeps back; the limit torch's allocator is held to |
| ways | Cheaper ways a node tries one step after running out of memory (`ctx.fallbacks`); `made_with` records the way each step finished |
| key | A cache entry's name, a hash of all that produced it; a value is `<key>:<port>` |
| data root, model store | The one folder everything is written under; and the part of it that holds model files, once each by sha256 |
| journal | The record of every event of a run, an install or a model job, kept before the event is sent |
| ending | A run's, install's or job's last event (`run.done`, `runtime.failed`, ...), sent after what it held is free |
| door | `engine:request`, the page's one way to the engine, through `app/main/door.js` |
| spike, report | A time-boxed question answered by throwaway code; the numbers from a real machine that a hardware claim needs |

## The parts

| Part | File | Job |
| --- | --- | --- |
| Port types | `contracts/port-types.json`, `oneframe/ports.py` | The data kinds, their facets, and the rule for connecting them |
| Manifests | `oneframe/manifest.py`, `oneframe/reading.py` | Read and check `node.json` part by part; every problem reported at once, a field of the wrong type among them, never a crash. `reading.py`, standard library only, is the one way a definition is read: a manifest, its memory model and a runtime's (the text field, the id rule, the version compare, a JSON object from a file, `ProblemsError`, `discover`) |
| Registry | `oneframe/registry.py` | Find nodes by looking in folders, through the `discover` runtimes use too; a broken node never hides the others, and is listed as a problem |
| Graph | `oneframe/graph.py` | Recipes (graphs as JSON, with the page's own `page` fields kept as they are); `plan()` checks them, inserts converters, orders them; `fits()` answers what a port could connect to; both follow one rule for an edge, `connect()` |
| Graphs | `oneframe/graphs.py`, `graphs/` | Graphs kept as files, `<name>.json`: the shipped ones in the folders `--graphs` names (the repo's `graphs/`), the person's in the data root's `graphs/`, which shadow a shipped one of their name; found by looking, a broken file listed as a problem |
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
| Server | `oneframe/server.py` | The engine's NDJSON protocol: one worker answers requests in order, and the loop that reads them answers the stops and the quick reads at once |
| Protocol | `contracts/protocol.json`, `oneframe/protocol.py`, `app/main/protocol.js`, `app/protocol.d.ts` | Every method and every event, declared once ([spec 018](../specs/018-protocol/spec.md)): the engine builds each event and checks each answer against it, a problem logged and kept, never refused; the door strips by it what never reaches the page; the page's types are generated from it (`scripts/protocol-types.js`) |
| Imports | `oneframe/inputs.py` | The files a graph reads, copied into the data root's `inputs/` by their sha256, which is their id: a `file` param holds the id, never a path |
| Benches | `oneframe/bench_runtime.py`, `oneframe/bench_fit.py`, `engine/tests/fit_check.py` | The reports a hardware claim needs: a runtime's install, a node's memory on a card, and spec 002's check on a card, which lives with its test node |
| Tool launcher | `scripts/tool.js` | Starts each command-line tool that opens a data root, with uv's temporary files under that root |
| Engine client | `app/main/engine.js` | The app's side of the protocol |
| Main | `app/main/main.js`, `app/main/boot.js`, `app/main/door.js`, `app/main/values.js` | Electron's folders under the root, the window, the engine process; the one IPC door, which answers with values, the app's own methods among them, and keeps every path from the page; the value protocol |
| Page | `app/renderer/`, `scripts/page-build.js` | The page ([spec 017](../specs/017-first-page/spec.md)): `main.js` draws it from plain modules the node tests import without a browser: the open graph and its edits (`graph-model.js`), a control per param type (`form.js`), a card's state from events and each failure's offer (`run-view.js`), numpy's `.npz` (`npz.js`), and a viewer per port type (`viewer.js`, `viewers/`); bundled with three.js into `dist/renderer/` |

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
included, but for the exceptions `outside` in `contracts/layout.json` lists, each with its writer,
its paths and why. A footprint test proves the engine's side by pointing every home and temporary
folder into a sandbox, where a write outside the root passes only at a path an entry names for what
the engine's process tree runs; Electron's folders rest on a unit test of
`app/main/boot.js` and a headless run. The open gaps are listed in the handoff.
Every kept file says its format, and an older app leaves a newer one alone. A format goes up only
when an older app would misread or damage the file, and the change that raises it brings the
migration; a new file or folder is not a format change. Processes on one root
lock what they work on, and the operating system releases a lock when its holder dies (on Windows,
a moment after).

**One door into the page.** The page is sandboxed, has no Node, and can call only the engine
methods `ENGINE_METHODS` lists and the app's own, `APP_METHODS` (`photo.pick`), through
`engine:request`; it reads a run's values through the read-only `oneframe-value://<key>/<port>`,
which serves nothing but a committed value. A compromised page can ask the engine to run a graph;
it cannot read a file, start a process, or learn a path: the door drops a local path from every
answer and event (a value's `path`, a manifest's `folder`, the hello's `data`), and writes the data
root, the repo and the home folder `<data>`, `<repo>` and `~` everywhere else.

**The page learns everything.** It names no node, graph, model or runtime
(`test_the_page_names_nothing`): the catalogue is `nodes.list`, a card's settings its manifest's
params, its wiring `graph.fits`, its state the run's events, typed from `app/protocol.d.ts`'s
`Page`. A failure's kind and reason choose its offer: Install for a runtime not installed or out of
date, Download for files not downloaded or incomplete, a wait for the job already mending it, Run
again for one that may succeed later. A viewer is chosen by the value's type, one module a type in
`app/renderer/viewers/`, and reads the value by its key; a point viewer draws at most
`POINT_BUDGET` points (a million) at a whole stride, and its camera is linked to the other point
panes'. The panes shown are the graph's `page.panes`, and the names of the photos picked
`page.files`, so a saved graph opens as it was left. The page is one bundle, `page.js`, built by
esbuild with three.js, so it needs no import map and its policy stays `script-src 'self'`; it is a
`file://` page, so the value scheme is CORS-enabled and only the page's partition has it. Three
type configs keep each environment to its own globals (`tsconfig.page.json` has the DOM and no
Node), and ESLint refuses `node:` and `electron` imports in the page.

**A photo comes in by its content.** `photo.pick` shows the system's open dialog in the main
process, which has the engine copy the file chosen into the root's `inputs/` (`inputs.import`,
named by its sha256) and answers the page that id and the file's name. A `file` param holds an
import's id, from any caller; anything else, a path included, is a problem of that param, and the
path is never looked at. The step is given the import's file, so a node reads a file as before. A
command-line tool given a path imports it first (`bench:fit --graph`, CI's model check).

**Requests off the input loop.** The engine answers requests in order on one worker thread, as its
methods were written to run. The loop that reads them answers `engine.hello`, `values.locate` and
the three stops (`run.stop`, `runtimes.stop`, `models.stop`) itself, so a slow request never holds
a stop up; a stop first takes each queued request of its kind off the queue and answers it
`{"stopped": true}`, never begun.

## Events

What the engine and the app say to the page is declared in `contracts/protocol.json`: every method,
its params and its answer, and every event, its fields and who sends it. That file is the
declaration and this section its reading; the list that ends it names every event, and a test holds
the two equal. A field marked `"page": false` (a value's `path`, a manifest's `folder`, the hello's
`data`) never reaches the page.

A run answers `graph.run` at once with a run id; everything after is pushed:

`run.start` → per node: `node.cached` (the result at its own values), or `node.fit` (the device,
precision, changes, needs, budgets and any warning: `slow`, `tried_anyway`, `unknown`) then
`node.cached` (a reduced result the fit allows) or `node.start` (with its `attempt`), then `stage` /
`progress` / `ceiling` / `node.step_oom` from the node, then `node.done` (peaks, `made_with`, the
fit) | `node.oom` and, once, another `node.fit` and `node.start` | `node.failed` (with the fits) →
`run.done` | `run.failed` | `run.stopped`. An engine node without a memory model gets no `node.fit`.

A failure stops only what depends on it. A step whose inputs come from a failed or skipped step
gets `node.skipped` (with `waited_on`, that step) and never starts; a value that does not fit its
input skips that step too, naming the `edge`. The run goes on with every other step, then ends
`run.failed` with the first failure, its step or edge, and `failed` and `skipped`, the steps of
each. A Stop still ends the whole run.

Graphs are files the engine finds, as it finds nodes: `graphs.list` (each graph's name, source,
`shipped` or `saved`, and title, and every broken file), `graphs.get {name}`,
`graphs.save {name, graph}` and `graphs.remove {name}`. A save writes the person's copy under the
data root, so a shipped graph is never changed and its name then opens the saved one; a remove
deletes only a saved graph, which the person asked for by name, and brings the shipped one back.
`graph.fits {graph, port}` answers, for a port of a node in the graph, each opposite port of the
graph's other nodes (`graph`) and of every installed node (`add`): `direct`, `via` a converter
(the one `plan()` inserts), `depends` (an output that leaves open a facet the input names; the
value is checked when it arrives), or `no`, with its reason: `mismatch`, `taken` (the input is
fed) or `cycle`.

`nodes.fit` answers the fit a node would get now, and `nodes.forget` clears what this machine
learned about a node; the page may ask the first. `values.locate {key, port}` answers the file of
a value a run committed under that entry key, for the main process's value protocol; `inputs.import
{path}` imports a file, for `photo.pick`. Neither of these two is on the page's list.

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
file. The page may ask `models.download` and `models.stop`; the rest are not on its list yet.

**An ending is sent after its place is free** (`journal.end_when_free`, spec 010). A run lets go of
its list of the files it reads and of the engine's run slot, an install of its slot and its
runtime's lock, and a job of the store's lock, each after its ending is in the journal and before it
is sent, an engine crash's ending too. So a page that acts on hearing one (a next run, the same
install again, removing the run's runtime or a file it read) is answered, not refused `busy` or
`in_use`. A next run's or job's first events may then come before the last one's ending, told apart
by `run` or `job`; a next install's never do, since an install lets go and sends under its claim
lock.

**Every event,** with its fields in `contracts/protocol.json`: `engine.ready`, `engine.failed`,
`engine.warning`, `engine.exit`, `run.start`, `run.done`, `run.failed`, `run.stopped`,
`node.cached`, `node.fit`, `node.start`, `stage`, `progress`, `ceiling`, `node.step_oom`,
`node.done`, `node.oom`, `node.failed`, `node.skipped`, `runtime.start`, `runtime.step`,
`runtime.log`, `runtime.progress`, `runtime.done`, `runtime.failed`, `runtime.stopped`,
`model.start`, `model.file`, `model.progress`, `model.done`, `model.failed`, `model.stopped`.

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
| `graph` | `newer_format` | no | A newer version of the app saved it; it is left as it is. |
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
