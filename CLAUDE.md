# Oneframe Lab

A local, single-photo 3D playground: Electron shell, a Python 3.14 engine run by uv, and nodes
(model adapters) declared by manifests.

**Start here:** [docs/handoff.md](docs/handoff.md) says what is done, what was decided, and what
comes next. Read [docs/architecture.md](docs/architecture.md) before changing anything
structural, and [docs/nodes.md](docs/nodes.md) before adding a node.

## Rules

- **Nothing hard-coded.** No code outside a node's folder names a node, a model, or a
  pipeline. The engine and app learn what exists from `nodes/*/node.json`; ports come from
  `contracts/port-types.json`. A new model is a new folder, never an edit to the engine or app.
- **Ports are typed, facets included.** Never connect values whose facets differ without a
  converter node. Disparity must never flow silently into a metric port.
- **Light engine.** Importing `oneframe.server` loads no numpy, Pillow, torch or other model
  library (a test holds this). Heavy code lives in nodes; model code runs in the node's runtime.
- **No network during a run.** Runtime children block sockets. Everything a node reads is fetched
  by Download first.
- **One door into the page.** The renderer is sandboxed and isolated and reaches the engine only
  through `engine:request` with a method from `ENGINE_METHODS` in `app/main/main.js`. Add a method
  there only when the engine has it and the page needs it. Progress is pushed as events; nothing
  polls.
- **Versions.** Latest stable everywhere, exact pins, and every exception written in
  `versions.json` with a reason and a review date. See [docs/versions.md](docs/versions.md).
  Do not add a dependency without checking its latest release.
- **Honest labels.** `trust` on an output is measured, predicted or synthetic. Never mark a
  generated result measured.
- `encoding="utf-8"` on every text open in Python; Windows defaults to cp1252.

## Commands

- `npm run check`: eslint, tsc (JSDoc types, `// @ts-check` in every JS file), node tests
- `npm run engine:check`: ruff, ruff format, pyright, pytest
- `npm run versions`: version policy
- `npm start`: the app (needs `npm run engine:sync` once)
