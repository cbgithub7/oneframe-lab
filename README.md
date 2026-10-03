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
node to the memory a machine has (spec 002), the Electron shell, and the checks. No real model has
run yet: the only nodes are a photo source and a depth-to-points converter. The model store (spec
003) is being specified, and the first real models follow it. [docs/handoff.md](docs/handoff.md)
has the state and what comes next.

## Layout

| Folder | What |
| --- | --- |
| `app/` | Electron: `main/` (process, engine client), `preload/` (the only bridge), `renderer/` (the page) |
| `engine/` | The Python engine, `oneframe` (Python 3.14, managed by uv) |
| `nodes/` | Built-in nodes, one folder each |
| `runtimes/` | The Python environments heavy nodes run in, one folder each |
| `contracts/` | Data both sides read: port types |
| `scripts/` | `check-versions.js` |
| `docs/` | Architecture, writing a node, version policy |

## Running

Needs [Node](https://nodejs.org) (the LTS in `.node-version`) and [uv](https://docs.astral.sh/uv/)
(0.12.20 or newer). uv fetches the right Python itself.

```
npm install
npm run engine:sync
npm start
```

Checks, the same ones CI runs on Windows and Linux:

```
npm run check          # eslint, tsc (TypeScript 7, JSDoc types), node --test
npm run engine:check   # ruff, pyright, pytest
npm run versions       # every pin against its latest release
```

A hardware claim needs numbers: `npm run bench:runtime -- <id>` installs a runtime on this machine
and writes a report of what it did.

## Documents

- [AGENTS.md](AGENTS.md): the rules and the way work is done, for people and agents
- [specs/README.md](specs/README.md): how a change goes from spec to merged PR
- [docs/handoff.md](docs/handoff.md): what is done, what was decided, what comes next
- [docs/architecture.md](docs/architecture.md): how the parts fit, and the rules that keep them apart
- [docs/nodes.md](docs/nodes.md): writing a node
- [docs/runtimes.md](docs/runtimes.md): writing a runtime
- [docs/versions.md](docs/versions.md): the version policy

## Licence

The app's own code is under the [MIT licence](LICENSE). Each model a node runs keeps its own
licence for its code and its weights, which the node's manifest states and the app shows; some
are for non-commercial use only.
