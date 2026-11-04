# 005: The first model nodes: Depth Pro and MoGe

Status: draft
Owner approval: (date, once approved)

The first of four specs for step 3, first light, which is too large for one: this one, then
[015](../015-graphs/spec.md) (graphs as data, what fits here), [016](../016-door/spec.md) (photos
and results through the door) and [017](../017-first-page/spec.md) (the page). It goes first
because it retires the largest risk left before the editor: whether real models install and run
here, on Windows and on an older card. It needs none of the others. It starts from the pins in
`specs/003-model-store/live/`, the [review of 2026-10-12](../../docs/reviews/2026-10-12.md)
(section 5, item 8) and the handoff's "pyright on the first node that imports torch".

## Problem

No node runs a model. The runtimes, the fit, the model store and the node API have run only test
nodes and a runtime with no model code. Neither family is on PyPI: Depth Pro pins `numpy<2`, which
has no wheels for Python 3.14, and MoGe 3.0's package pins torch to its own CUDA index and requires
FlexGEMM, built on Triton, which `moge/model/v3.py` imports at the top. FlexGEMM's kernels run only
on cards Triton supports (compute 8.0 and newer for what it uses), and only in MoGe-3's refinement.
`bench:fit` measures a node alone in a graph with no edges, so it cannot measure a node that needs
an image.

## Requirements

1. **Two runtimes, one per family:** `runtimes/depth_pro/` and `runtimes/moge/`, with the upstream
   code as pinned `sources` (a GitHub archive at a full commit) and the upstream's dependencies
   listed by the runtime itself, so no upstream index pin or optional package enters the lock.
2. **Three nodes:** `depth.depth_pro` (an image and an optional camera → metric z-depth and the
   camera it used), `depth.moge3` (MoGe-3 ViT-L) and `depth.moge2` (MoGe-2 ViT-L with normals),
   each MoGe an image → a metric camera-frame point map, metric z-depth, a valid mask, the
   estimated camera, and normals. Ports say exactly what each value is.
3. **Each node loads once, then runs** (`load` and `run`), on `cuda` or `cpu`, at `fp32` or `fp16`
   where the model supports it, reading its weights only through `ctx.file`.
4. **No build installs FlexGEMM yet.** Every build of `moge` carries a stand-in for `flex_gemm`
   that imports and raises on use, and MoGe-3 runs without its refinement steps, saying so in its
   outputs' `meta` (`refine_steps: 0`), until a card that can run it reports (open question 1).
5. **`bench:fit` measures a node inside a graph** the person gives (`--graph <file> <key>`), so
   its inputs are real values; without `--graph`, a node alone, as today.
6. **Memory models from measurement:** each node's weights per precision and its working memory at
   three settings, from `bench:fit` on a real card, each figure naming its report; `null`, with a
   source saying so, until measured.
7. **CI runs each node on the processor** with real torch and the real weights, on Windows and
   Ubuntu, and pyright checks the nodes' code against their runtimes' packages.

## Acceptance criteria

- [ ] AC1: `test_model_nodes_are_well_formed` (`test_model_nodes.py`): the registry and the runtime
  manager read the three manifests and two definitions with no problem (the readers already refuse
  an unpinned file and a malformed port).
- [ ] AC2: The CI job `models (cpu)`, on Windows and Ubuntu, installs both runtimes' `cpu` builds,
  downloads the nodes' files with `npm run models:download` (cached by sha256), and runs
  `engine/tests/models/run_on_cpu.py`: each node through the scheduler on a 512 px photo, each
  output checked against its port (type, facets, shape, finite where valid, depths 0.1 to 100 m,
  `trust` `predicted`). Fails today: no such nodes.
- [ ] AC3: The same job runs pyright over `nodes/depth.*` with each runtime's interpreter, with
  no error.
- [ ] AC4: `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`): `--graph` with a test
  graph of an image source and a test node that needs an image measures the test node at each
  `--set` group, the source cached and the measured node from an empty cache. Fails today: no
  `--graph`.
- [ ] AC5 (hardware): `npm run bench:runtime -- depth_pro` and `-- moge` on the owner's PC
  (Windows, GTX 1070) install and probe the build the plan picks; the reports give each install's
  seconds and disk.
- [ ] AC6 (hardware): `npm run bench:fit` for each node on the same PC, at the settings below,
  finishes with every measured peak at most its estimate once the manifests hold the figures it
  measured; the report gives seconds, peaks and fits, and whether MoGe-3 ran, and how.

## Design

**Runtimes.** Each folder follows `docs/runtimes.md`. `pyproject.toml` lists the upstream's runtime
dependencies at their newest releases with cp314 wheels on both systems (Depth Pro's `timm`,
`pillow-heif`, `numpy>=2`; MoGe's `opencv-python-headless`, `scipy`, `trimesh`, `utils3d` and the
rest it imports for inference, not `gradio`, `huggingface-hub` or its CLI's), torch at the newest
release with cp314 wheels on all three indexes, and `torchvision` with it. A git dependency of the
upstream (`utils3d_moge`) is a pinned `source` too. FlexGEMM is an extension of class `stand-in`:
a `flex_gemm` package in the runtime's folder with the names `v3.py` imports, each raising on use.
A pin below its latest goes in `versions.json` with its reason.

**`bench_fit.py`** gains `--graph <file> <key>`: it reads the graph with `Graph.from_json`, runs the
steps before `<key>` once, cached, and measures `<key>` from an empty cache at each group; the report
names the graph and each input's size. Nothing in the bench names a node. The bench graphs live in
`specs/005-first-models/bench/`.

**Nodes.** `load(ctx)` builds the model from the upstream code with no pretrained fetch, loads
`ctx.file(...)` with `weights_only` as forced, and moves it to `ctx.device` and `ctx.precision`.
`run(ctx, model)` reads the PNG, infers, and writes the `.npz` and `.json` of `docs/nodes.md`'s
conventions. Depth Pro's network runs at a fixed 1536 px, so its only param is `precision`; its
camera is its own focal estimate (`source=estimated`), or the input's when one is connected. MoGe's
params are `resolution_level` and `precision`; its masked pixels (`inf`) are `valid=False`, its
intrinsics turned from fractions to pixels. MoGe-3 runs with `refine_steps=0` and writes it in each
output's `meta` (the engine owns `made_with`). Licences are shown, never used to block: the code's
from upstream (Depth Pro's Apple sample-code licence; MoGe's MIT, DINOv2's Apache-2.0); the weights'
from their own pages, read where they are reachable (Hugging Face lists Depth Pro's as Apple's
research licence, `apple-amlr`, which the local session confirms against Apple's page).

**Memory models** start with weights from the pinned sizes and working memory `null`; AC7's report
fills them, and the PR that commits it commits the figures.

**CI** (`.github/workflows/checks.yml`): a job `models (cpu)` beside `checks`, on both systems, with
`timeout-minutes`, on every pull request, so it can be a required check. The weights are cached once
for both systems (`enableCrossOsArchive`, keyed by the manifests' sha256s, about 4.5 GB of GitHub's
10 GB per repository), and uv's cache per system by the locks.

**Docs:** `docs/nodes.md` (`bench:fit --graph`), `docs/runtimes.md` (a stand-in for an import a
model needs only for an optional step), `AGENTS.md`'s `bench:fit` line, and the handoff's MoGe item.

Risks: Depth Pro on numpy 2, untested upstream (AC2 runs it); the stand-in not matching what
`v3.py` builds at import (AC2 imports it); a runner's disk too small for both runtimes and the
weights (the job frees the runner's preinstalled tools first if it must).

## Tests

- Added: `test_model_nodes_are_well_formed` (`test_model_nodes.py`, new);
  `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`); the CI job and its
  script `engine/tests/models/run_on_cpu.py`, which pytest does not collect.
- Removed: none
- Renamed: none

## Verification

AC1 and AC4 run in `npm run engine:check`; AC2 and AC3 in CI's `models (cpu)`. AC5 and AC6, in
a local session on the owner's PC (Windows, GTX 1070), from the branch, with `ONEFRAME_DATA` at a
new empty folder:

1. `npm run bench:runtime -- depth_pro`, then `npm run bench:runtime -- moge`.
2. `npm run models:download -- depth.depth_pro`, `-- depth.moge3`, `-- depth.moge2`.
3. `npm run bench:fit -- --graph specs/005-first-models/bench/depth_pro.json depth --set
   precision=fp32 --set precision=fp16`, once with the graph's 1536 px photo and once with its
   768 px one; `npm run bench:fit -- --graph specs/005-first-models/bench/moge3.json depth --set
   resolution_level=0 --set resolution_level=5 --set resolution_level=9`, and the same for MoGe-2.
4. Copy the reports to `specs/005-first-models/reports/`, write the figures into the manifests,
   run step 3 again to show every peak within its estimate, and commit.

MoGe-3's refinement is not shipped, so nothing about it is claimed; a build with FlexGEMM comes
with a report from a card of compute 8.0 or newer (the Improve-loop spike's rented GPU can give one).

## Decisions taken

- Upstream code as `sources`, its dependencies listed by the runtime: neither family is on PyPI,
  and MoGe's own package would bring its torch index, FlexGEMM and gradio into every build.
- Depth Pro on `numpy>=2`: Python 3.14 has no numpy 1 wheels; AC2 shows whether it works.
- Two MoGe nodes, not one with a choice: their code and outputs differ, and ports are fixed per node.
- MoGe-2 and MoGe-3 share the `moge` runtime: one upstream holds both.
- No FlexGEMM build until a card that can run it reports: nothing here could test one, and a
  stand-in for every build needs no new runtime mechanism.
- `bench:fit --graph`, not a flag that builds an input value: a graph is how a node gets real
  inputs, and the bench then names no source node.
- CI runs the real weights, not a random-weight model, so it checks the files and the loading.

## Out of scope

- The page, graphs and results: 015 to 017.
- MoGe-3 ViT-G, other depth families: a node each, later. Keeping models loaded: spec 004.

## Open questions

1. **MoGe-3 without its refinement**, on every machine until a card of compute 8.0 or newer
   reports: MoGe-3's network, labelled `refine_steps: 0`, but not the model as published.
   Recommended: ship it so, beside MoGe-2, and the shipped graph uses it. The other choice: no
   MoGe-3 node yet, and first light uses MoGe-2 (the owner's fallback of 2026-10-11).
2. **The weights' licences:** Depth Pro's research licence (`apple-amlr`, not for commercial use),
   and MoGe's as their model cards say. The app is personal and non-commercial, and licences are
   shown, never block (the owner, 2026-10-12). Recommended: ship both in the default graph, with
   the licences on the node's card.
