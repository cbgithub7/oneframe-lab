# Oneframe Lab: rules for coding agents

A local, single-photo 3D playground: Electron shell, a Python 3.14 engine run by uv, and nodes
(model adapters) declared by manifests. This file is the shared rule set for any agent; keep it
short and true. Changing a rule, a command or the layout means updating this file in the same
change -- a stale rules file is worse than none.

**Start here:** [docs/handoff.md](docs/handoff.md) (state, decisions, what is next), then the
spec you are working on in `specs/`. Read [docs/architecture.md](docs/architecture.md) before
changing anything structural, and [docs/nodes.md](docs/nodes.md) before adding a node.

## How work is done

1. **Spec first.** Anything larger than a small fix has a folder in `specs/` with `spec.md`,
   `plan.md` and `tasks.md` ([specs/README.md](specs/README.md)). The owner approves the spec and
   the plan before code is written. The spec, not the chat, is the source of truth.
2. **Small steps.** Work through `tasks.md` in order, ticking each item as it lands.
3. **Verify before claiming.** `npm run check` and `npm run engine:check` pass before a turn ends
   and before a PR. Acceptance criteria in the spec are checked one by one in the PR.
4. **Tests are not negotiable.** Never delete or weaken a test to make a check pass. A test may
   be removed only when the spec's plan lists it under "Tests removed", with the reason.
   `node scripts/test-guard.js` enforces this in the hooks and in CI.
5. **The PR is where the owner decides.** An agent's job ends when it opens the pull request.
   Nothing merges to `main` except through a PR with green CI.
6. **Hardware claims need a report.** Nothing is called tested or working on a GPU without
   numbers from a real run on real hardware (seconds, peak VRAM, the arrangement that finished).
   Cloud sessions have no GPU; say plainly what is unverified.

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
  through `engine:request` with a method from `ENGINE_METHODS` in `app/main/main.js`. Progress is
  pushed as events; nothing polls.
- **Versions.** Latest stable everywhere, exact pins, and every exception written in
  `versions.json` with a reason and a review date ([docs/versions.md](docs/versions.md)). Check
  the latest release before adding any dependency.
- **Honest labels.** `trust` on an output is measured, predicted or synthetic. Never mark a
  generated result measured.
- `encoding="utf-8"` on every text open in Python; Windows defaults to cp1252.

## Commands

- `npm run check`: eslint, tsc (JSDoc types, `// @ts-check` in every JS file), node tests, and
  the docs check (every repo path the docs name exists)
- `npm run engine:check`: ruff, ruff format, pyright, pytest
- `npm run versions`: version policy
- `npm run bench:runtime -- <id>`: install a runtime on this machine and write a report of what it
  did (the numbers a hardware claim needs)
- `node scripts/test-guard.js`: no test removed without a listed reason
- `npm start`: the app (needs `npm run engine:sync` once)
