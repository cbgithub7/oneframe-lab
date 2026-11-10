# Oneframe Lab

A local playground for making 3D content from a single photo.

Every method is a node: depth models, image-to-mesh and image-to-splat generators, segmentation,
view synthesis, reconstruction from views, repair, and evaluation. A pipeline is a graph of nodes
that a person builds, saves and shares. Nothing is hard-coded: adding a model is adding a folder
with a `node.json` and its code.

Two loops:

1. **Generate.** Photo → any direct method: a 2.5D scene from depth, a mesh, a splat, segmented
   objects placed in a scene.
2. **Improve.** Photo, or a first result rendered along a camera path → synthesised extra views →
   reconstruction → a better result, compared against the first.

Everything runs on this PC. Nothing downloads during a run, and nothing downloads at all unless a
person presses Download.

## Status

The engine (node manifests, typed ports, graph planning, a content-addressed cache, and a scheduler
that runs nodes in-process or in their own runtime), the runtime manager (spec 001), fitting each
node to the memory a machine has (spec 002), the model store (spec 003), the first model nodes,
Depth Pro, MoGe-3 and MoGe-2 (spec 005), graphs as data (spec 015), and the first page (spec 017):
a graph opened, changed and run, its runtimes installed and its files downloaded from the page's own
offers, and its results side by side.
[docs/handoff.md](docs/handoff.md) has the state and what comes next.

## Layout

| Folder | What |
| --- | --- |
| `app/` | Electron: `main/` (process, engine client), `preload/` (the only bridge), `renderer/` (the page, bundled into `dist/renderer/`) |
| `engine/` | The Python engine, `oneframe` (Python 3.15, managed by uv) |
| `nodes/` | Built-in nodes, one folder each |
| `runtimes/` | The Python environments heavy nodes run in, one folder each |
| `contracts/` | Data both sides read: port types, and the data root's layout |
| `scripts/` | The checks (`check-versions.js`, `check-docs.js`, `test-guard.js`) and `tool.js`, which starts the command-line tools |
| `graphs/` | The graphs that ship, starting points saved as data |
| `tests/` | The app's node tests (`app/`) and the e2e test (`e2e/`) |
| `docs/` | Architecture, writing a node, version policy |

## Running

Needs [Node](https://nodejs.org) (the LTS in `.node-version`) and [uv](https://docs.astral.sh/uv/)
(0.13.5 or newer). uv fetches the right Python itself.

```
npm install
npm run engine:sync
npm start
```

## Commands

The checks are the ones CI runs on Windows and Linux.

- `npm run check`: eslint, the page's types against the contract, tsc (JSDoc types,
  `// @ts-check` in every JS file), node tests, and the docs check (every link in the docs leads
  somewhere, every repo path they name exists outside specs and the dated reviews in
  `docs/reviews/`, and no spec being worked on is over 200 lines, or over the length the owner
  allowed it)
- `node scripts/protocol-types.js [--write]`: `app/protocol.d.ts`, the page's types, held to what
  `contracts/protocol.json` gives, naming the lines that differ; `--write` writes it again, so a
  change to the contract commits the file (spec 018)
- `npm run engine:check`: ruff, ruff format, pyright, `runtime:check`, pytest
- `npm run runtime:check [-- --every-runtime]`: each runtime's code, and its nodes', held to a
  `ruff.toml` for its runtime's Python, and checked by pyright with that runtime's interpreter
  where it is installed; with `--every-runtime`, one not installed is a failure (spec 005)
- `npm run versions`: version policy
- `npm run bench:runtime -- <id>`: install a runtime on this machine and write a report of what it
  did (the numbers a hardware claim needs)
- `npm run bench:fit -- <node> [--set k=v ...]...`: run a node at each group of settings and report
  its estimates beside the peaks measured, the device's and the system's; with `--graph <file>`,
  `<node>` is a step of that graph, fed its real inputs
- `npm run bench:fit-check`: spec 002's hardware check on this machine's card, run from
  `engine/tests/fit_check.py` with the test node beside it
- `npm run models:pin -- [--nodes <folder>] <node>`: resolve a node's file sources to commits, fetch
  each file through the model store, and write its size, sha256, format and `gated` into the manifest
- `npm run models:download -- [--nodes <folder>] <node> [--checkpoint c | --all]`: fetch a node's
  files into the store, as `models.download` does; Ctrl+C stops it and the next run resumes. Both
  model tools wait up to 10 s for a store another process still holds (on Windows, npm gives the
  prompt back before a stopped tool has ended)
- `npm run diagnose`: one file in the data root's `reports/` to hand over when something failed:
  versions, machine, runtimes, settings, what was learned, the latest journals and logs, with the
  user's name taken out of every path and hub tokens scrubbed
- `node scripts/test-guard.js [--write]`: no test or case removed, skipped, emptied or renamed
  without a line in a spec the branch changed. It compares the engine's collected IDs with
  `engine/tests/collected.txt` at the merge base, and fails when the list in the checkout differs
  from the collection; `--write` rewrites it, so a change that adds or renames a test commits the
  list
- `node scripts/tool.js engine/tests/models/run_on_cpu.py`: CI's `models (cpu)` check, once every
  runtime and every node's files are in: each node that runs in a runtime, fed the 512 px photo,
  ends `done` on the processor with every valid depth between 0 and 1,000 m (spec 005)
- The tools that open a data root (`bench:runtime`, `bench:fit`, `bench:fit-check`, `diagnose`,
  `models:*`, `runtime:check`, `run_on_cpu.py`) start through `scripts/tool.js`, which keeps uv's
  temporary files under that root
- `npm run page:build`: the page, bundled by esbuild with three.js into `dist/renderer/`
  (`scripts/page-build.js`), which the app loads; `npm start` and `npm run e2e` run it first
- `npm run e2e`: the e2e test (`tests/e2e/`, `playwright.config.js`): Playwright starts the app on
  an empty data root with the test nodes and graphs of `tests/app/fixtures/`, runs a graph and
  stops one; on Linux it needs a display (`xvfb-run -a npm run e2e`), and in a container that runs
  as root, `ONEFRAME_NO_SANDBOX=1`. It is not part of `npm run check`; CI runs it on both systems
- `npm start`: the app (needs `npm run engine:sync` once); `ONEFRAME_NODES` and `ONEFRAME_GRAPHS`
  add folders of the person's own nodes and graphs ([docs/nodes.md](docs/nodes.md#folders-of-your-own))

## Documents

- [AGENTS.md](AGENTS.md): the rules and the way work is done, for people and agents, each with what holds it
- [specs/README.md](specs/README.md): how a change goes from spec to merged PR
- [docs/handoff.md](docs/handoff.md): what it is, what is done, what comes next and what is in the way
- [docs/roadmap.md](docs/roadmap.md): what is decided or found but parked
- [docs/architecture.md](docs/architecture.md): how the parts fit, and the rules that keep them apart
- [docs/nodes.md](docs/nodes.md): writing a node
- [docs/runtimes.md](docs/runtimes.md): writing a runtime
- [docs/versions.md](docs/versions.md): the version policy

## Licence

The app's own code is under the [MIT licence](LICENSE). Each model a node runs keeps its own
licence for its code and its weights, which the node's manifest states and the app shows; some
are for non-commercial use only.
