# Oneframe Lab: rules for coding agents

A local, single-photo 3D playground: Electron shell, a Python 3.14 engine run by uv, and nodes
(model adapters) declared by manifests. This file is the shared rule set for any agent; keep it
short and true. Changing a rule, a command or the layout means updating this file in the same
change -- a stale rules file is worse than none.

**Start here:** [docs/handoff.md](docs/handoff.md) (state, decisions, what is next), then the
spec you are working on in `specs/`. Read [docs/architecture.md](docs/architecture.md) before
changing anything structural, and [docs/nodes.md](docs/nodes.md) before adding a node.

## How work is done

1. **Spec first, in one document.** Anything larger than a small fix has a folder in `specs/` with
   `spec.md`, which holds the plan too, and `tasks.md` ([specs/README.md](specs/README.md)). Aim
   for about 150 lines and at most 8 acceptance criteria: mechanisms belong in code and tests, not
   written twice. An agent reviews the draft; the owner approves it once, before code is written.
   The spec, not the chat, is the source of truth.
2. **Agents decide what can be undone.** An agent makes reversible choices itself and logs each
   under "Decisions taken" in the spec. The owner decides product behaviour, licences and money,
   the platform matrix, data formats that are hard to change, and these rules.
3. **Spikes answer feasibility.** A question such as "does this model run on Windows" is a spike:
   time-boxed, no spec, a throwaway branch, ending in a report the owner reads.
4. **Small steps.** Work through `tasks.md` in order, ticking each item as it lands.
5. **Verify before claiming.** `npm run check` and `npm run engine:check` pass before a turn ends
   and before a PR. Acceptance criteria in the spec are checked one by one in the PR.
6. **Tests are not negotiable.** Never delete or weaken a test to make a check pass. A test may
   be removed only when the spec lists it under "- Removed", with the reason.
   `node scripts/test-guard.js` enforces this in the hooks and in CI.
7. **The PR is where the owner decides.** An agent's job ends when it opens the pull request.
   Nothing merges to `main` except through a PR with green CI.
8. **Hardware claims need a report.** Nothing is called tested or working on a GPU without
   numbers from a real run on real hardware (seconds, peak VRAM, the fit that finished). A report
   from any real machine counts, the owner's or a rented one, and names it. Cloud sessions have no
   GPU; say plainly what is unverified.

## Rules

- **Nothing hard-coded.** No code outside a node's folder names a node, a model, or a
  pipeline. The engine and app learn what exists from `nodes/*/node.json`; ports come from
  `contracts/port-types.json`. A new model is a new folder, never an edit to the engine or app.
- **Any card that can run it.** The app targets every machine that can run a model, from no GPU
  to the largest cards. The owner's GTX 1070 is the only card we can test on; it is never the
  target. No default, margin, threshold, formula or model choice is tuned to it, and nothing
  decides on a card's name: hardware decisions come from what the machine reports (compute
  capability, total and free memory, driver). A test of a hardware decision covers a range of
  machines, not one card. A report from the 1070 proves a mechanism works; it tunes nothing.
- **Ports are typed, facets included.** Never connect values whose facets differ without a
  converter node. Disparity must never flow silently into a metric port.
- **Light engine.** Importing `oneframe.server` loads no numpy, Pillow, torch or other model
  library (a test holds this). Heavy code lives in nodes; model code runs in the node's runtime.
- **No network during a run.** Runtime children refuse Python-level connections, are told hub
  libraries are offline, and get no hub token. This stops accidents, not malicious code. Everything
  a node reads is fetched by Download first.
- **One door into the page.** The renderer is sandboxed and isolated and reaches the engine only
  through `engine:request` with a method from `ENGINE_METHODS` in `app/main/methods.js`. Progress is
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
- `npm run bench:fit -- <node> [--set k=v ...]...`: run a node at each group of settings and report
  its estimates beside the peaks measured; with no node, spec 002's hardware check
- `node scripts/test-guard.js`: no test removed without a listed reason
- `npm start`: the app (needs `npm run engine:sync` once)
