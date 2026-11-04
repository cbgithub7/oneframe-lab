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
FlexGEMM, built on Triton, which `moge/model/v3.py` imports at the top. Triton has no wheels for
Pascal cards and needs compute 8.0 for what FlexGEMM runs.

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
4. **FlexGEMM is chosen by what the machine reports.** The `moge` runtime has a build with
   FlexGEMM for cards of compute 8.0 and newer, and builds without it for the rest and the
   processor. Without it, MoGe-3 runs without its refinement steps and says so in
   `meta.made_with` (open question 1).
5. **A runtime names its libraries' own caches,** which the engine places under the runtime's
   cache folder in the root, as it does for Hugging Face's and Triton's (FlexGEMM writes its
   autotune cache to the home folder by default).
6. **Memory models from measurement:** each node's weights per precision and its working memory at
   three settings, from `bench:fit` on a real card, each figure naming its report; `null`, with a
   source saying so, until measured.
7. **CI runs each node on the processor** with real torch and the real weights, on Windows and
   Ubuntu, and pyright checks the nodes' code against their runtimes' packages.

## Acceptance criteria

- [ ] AC1: `test_model_nodes_are_well_formed` (`test_model_nodes.py`): the three manifests and two
  runtime definitions read with no problem, every file is pinned, and every output names each
  facet with one value or is one the node sets.
- [ ] AC2: The CI job `models (cpu)`, on Windows and Ubuntu, installs both runtimes' `cpu` builds,
  downloads the nodes' files with `npm run models:download` (cached by sha256), and runs
  `engine/tests/models/run_on_cpu.py`: each node through the scheduler on a 512 px photo, each
  output checked against its port (type, facets, shape, finite where valid, depths 0.1 to 100 m,
  `trust` `predicted`). Fails today: no such nodes.
- [ ] AC3: The same job runs pyright over `nodes/depth.*` with each runtime's interpreter, with
  no error.
- [ ] AC4: `test_moge_builds_follow_the_card` (`test_runtime_plan.py`): the `moge` runtime plans
  its FlexGEMM build for a card of compute 8.6 on Windows and on Linux, a build without it for 6.1
  and 7.5, and `cpu` without a card; none is blocked.
- [ ] AC5: `test_runtime_library_caches_stay_under_the_root` (`test_child_env.py`): a runtime's
  declared cache variable reaches its child under `cache/runtime/<id>/`; one that names a path
  outside it, or a variable the engine sets or removes, is refused as a problem of the definition.
- [ ] AC6 (hardware): `npm run bench:runtime -- depth_pro` and `-- moge` on the owner's PC
  (Windows, GTX 1070) install and probe the build the plan picks; the reports give each install's
  seconds and disk.
- [ ] AC7 (hardware): `npm run bench:fit` for each node on the same PC, at the settings below,
  finishes with every measured peak at most its estimate once the manifests hold the figures it
  measured; the report gives seconds, peaks and fits, and whether MoGe-3 ran, and how.

## Design

**Runtimes.** Each folder follows `docs/runtimes.md`. `pyproject.toml` lists the upstream's runtime
dependencies at their newest releases with cp314 wheels on both systems (Depth Pro's `timm`,
`pillow-heif`, `numpy>=2`; MoGe's `opencv-python-headless`, `scipy`, `trimesh`, `utils3d` and the
rest it imports for inference, not `gradio`, `huggingface-hub` or its CLI's), torch at the newest
release with cp314 wheels on all three indexes, and `torchvision` with it. A git dependency of the
upstream (`utils3d_moge`, FlexGEMM) is a pinned `source` too. `moge`'s builds, fastest first:
`cu130-refine` (compute 8.0 and newer, with FlexGEMM and Triton, `triton-windows` on Windows), then
`cu130`, `cu126` and `cpu`, which carry a `stand-in` for `flex_gemm` that imports and raises on use.
A pin below its latest goes in `versions.json` with its reason.

**`runtime.json` gains `caches`**: variable → a path inside the runtime's cache folder
(`{"FLEX_GEMM_AUTOTUNE_CACHE_PATH": "flex_gemm/autotune.json"}`), checked in `definition.py`, set
by `environment.py` beside `LIBRARY_CACHES`, so the engine names no library a runtime brings.

**Nodes.** `load(ctx)` builds the model from the upstream code with no pretrained fetch, loads
`ctx.file(...)` with `weights_only` as forced, and moves it to `ctx.device` and `ctx.precision`.
`run(ctx, model)` reads the PNG, infers, and writes the `.npz` and `.json` of `docs/nodes.md`'s
conventions. Depth Pro's network runs at a fixed 1536 px, so its only param is `precision`; its
camera is its own focal estimate (`source=estimated`), or the input's when one is connected. MoGe's
params are `resolution_level` and `precision`; its masked pixels (`inf`) are `valid=False`, its
intrinsics turned from fractions to pixels. MoGe-3 asks for no refinement where the build has none.
Licences come from upstream (Depth Pro's code and weights under Apple's sample-code licence, MoGe's
code MIT with DINOv2 Apache-2.0); the weights' licences are read from their model cards when pinned
again where Hugging Face is reachable, shown and never used to block.

**Memory models** start with weights from the pinned sizes and working memory `null`; AC7's report
fills them, and the PR that commits it commits the figures.

**CI** (`.github/workflows/checks.yml`): a job `models (cpu)` beside `checks`, on both systems, with
`timeout-minutes`, caching `models/blobs` by the manifests' sha256s and uv's cache by the locks; it
runs when `nodes/`, `runtimes/` or `engine/` change, and on `main`.

Risks: Depth Pro on numpy 2, untested upstream (AC2 runs it); the stand-in not matching what
`v3.py` builds at import (found when it is written, and AC2 imports it); `cu130-refine` is checked
only by its plan (AC4) until a card of compute 8.0 runs it (Verification).

## Tests

- Added: `test_model_nodes_are_well_formed` (`test_model_nodes.py`, new);
  `test_moge_builds_follow_the_card` (`test_runtime_plan.py`);
  `test_runtime_library_caches_stay_under_the_root` (`test_child_env.py`); the CI job and its
  script `engine/tests/models/run_on_cpu.py`, which pytest does not collect.
- Removed: none
- Renamed: none

## Verification

AC1, AC4 and AC5 run in `npm run engine:check`; AC2 and AC3 in CI's `models (cpu)`. AC6 and AC7, in
a local session on the owner's PC (Windows, GTX 1070), from the branch, with `ONEFRAME_DATA` at a
new empty folder:

1. `npm run bench:runtime -- depth_pro`, then `npm run bench:runtime -- moge`.
2. `npm run models:download -- depth.depth_pro`, `-- depth.moge3`, `-- depth.moge2`.
3. `npm run bench:fit -- depth.depth_pro --set precision=fp32 --set precision=fp16`, with a 1536 px
   and a 768 px photo; `npm run bench:fit -- depth.moge3 --set resolution_level=0 --set
   resolution_level=5 --set resolution_level=9`, and the same for `depth.moge2`.
4. Copy the reports to `specs/005-first-models/reports/`, write the figures into the manifests,
   run step 3 again to show every peak within its estimate, and commit.

The refinement build stays unverified until a card of compute 8.0 or newer runs step 3 for
`depth.moge3` (the Improve-loop spike's rented GPU can); the PR says so.

## Decisions taken

- Upstream code as `sources`, its dependencies listed by the runtime: neither family is on PyPI,
  and MoGe's own package would bring its torch index, FlexGEMM and gradio into every build.
- Depth Pro on `numpy>=2`: Python 3.14 has no numpy 1 wheels; AC2 shows whether it works.
- Two MoGe nodes, not one with a choice: their code and outputs differ, and ports are fixed per node.
- MoGe-2 and MoGe-3 share the `moge` runtime: one upstream holds both.
- FlexGEMM by build, not by a check in the node: the plan already decides from compute capability.
- A runtime's library caches as data in `runtime.json`, not names in `environment.py`.
- CI runs the real weights, not a random-weight model, so it checks the files and the loading.

## Out of scope

- The page, graphs and results: 015 to 017.
- MoGe-3 ViT-G, other depth families: a node each, later. Keeping models loaded: spec 004.

## Open questions

1. **MoGe-3 without its refinement** where FlexGEMM cannot run (every card below compute 8.0, the
   1070 among them, and the processor): it is still MoGe-3's network, labelled in `made_with`,
   but not the model as published. Recommended: run it so, labelled, beside MoGe-2; the other
   choice is a MoGe-3 node refused there, leaving MoGe-2 (the owner's fallback of 2026-10-11).
