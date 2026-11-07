# 005: The first model nodes: Depth Pro and MoGe

Status: approved (2026-11-05)
Owner approval: 2026-11-05, every open question answered (below)

The first of four specs for step 3, first light, before [015](../015-graphs/spec.md) (graphs as
data), [016](../016-door/spec.md) (photos and results through the door) and
[017](../017-first-page/spec.md) (the page), needing none of them. It starts from the pins in
`specs/003-model-store/live/`, the reviews of [2026-10-12](../../docs/reviews/2026-10-12.md) (5.8)
and [2026-11-04](../../docs/reviews/2026-11-04.md) (2.7), and its spike on the owner's GTX 1070 on
Windows ([report](reports/2026-11-03-spike.md); "finding N" below is its).

## Problem

No node runs a model. The spike showed that real ones can, on Windows and on the 1070: both
families install from their GitHub code with Python 3.14 and torch 2.14.0, no FlexGEMM, gradio or
upstream torch index; Depth Pro runs on numpy 2; MoGe-3 runs without its refinement; the weights
load from the store with `weights_only` forced and connections refused. It also found Depth Pro at
fp32 too big for an 8 GB card, its fp16 load peaking at fp32's size, and MoGe's fp16 saving nothing
there. `bench:fit` cannot measure a node that needs an image, and runtime code is linted for 3.15.

## Requirements

1. **Two runtimes, one per family:** `runtimes/depth_pro/` and `runtimes/moge/`, on the Python and
   torch of `runtimes/torch`, with the upstream code as pinned `sources` and its dependencies listed
   by the runtime, so no upstream index pin or optional package enters the lock.
2. **Three nodes,** each from an `Image`: `depth.depth_pro` (and an optional `Camera`) gives
   `Depth[kind=metric, measure=z]` and the `Camera[model=pinhole]` it used; `depth.moge3` (MoGe-3
   ViT-L) and `depth.moge2` (MoGe-2 ViT-L) each give `PointMap[frame=camera, scale=metric]`, that
   `Depth`, `Mask[kind=binary, marks=valid]`, `Camera[model=pinhole, source=estimated]` and
   `Normals[frame=camera_opencv]`.
3. **Each node loads once, then runs** (`load` and `run`), on `cuda` or `cpu`, at `fp32` or `fp16`,
   reading its weights only through `ctx.file`. An fp16 load never holds fp32 weights on the card.
4. **No build installs FlexGEMM yet.** Every `moge` build carries a stand-in for `flex_gemm` that
   imports and raises on use; MoGe-3 runs without its refinement and says so in its outputs'
   `meta` (`refine_steps: 0`) until a card of compute 8.0 or newer reports.
5. **`bench:fit` measures a node inside a graph** (`--graph <file>`), so its inputs are real, and
   gives each run's system estimate and peak beside the device's; without `--graph`, as today.
6. **Memory models from measurement,** each figure naming its report and card: weights per
   precision, working memory over the settings and the photo's size, and system memory; none assumed
   from the precision.
7. **CI runs each node on the processor** with real torch and weights, on Windows and Ubuntu.
8. **Runtime code is checked at its runtime's Python:** a runtime's folder and its nodes, by ruff
   for that Python and by pyright with that runtime's interpreter and packages. Nothing outside a
   node's folder names the node.
9. **The test card is a bench, not the target** (the owner, 2026-11-05): the 1070's runs prove
   mechanisms and measure the models' own figures, naming the card; no default, setting, `changes`
   order, bound or test expectation is chosen to suit it.

## Acceptance criteria

- [x] AC1: `test_the_model_nodes_fit_a_range_of_machines` (`test_model_nodes.py`): fitted on every
  machine of `engine/tests/fixtures/machines.py` (no card; 2 to 80 GB at compute 5.2 to 9.0), Depth
  Pro runs at fp32 on each card its fp32 estimate fits, else on the processor, offering fp16 where
  fp16 fits; MoGe at its defaults on each card that holds them; renaming the cards changes nothing.
- [x] AC2: The CI job `models (cpu)`, on Windows and Ubuntu, installs every runtime's `cpu` build
  and the nodes' files (`npm run models:download`, cached by sha256), and runs
  `engine/tests/models/run_on_cpu.py`, which feeds the 512 px photo to every node of those runtimes:
  each run ends `done` (the scheduler checks every output against its port), and every valid depth
  lies above 0 and below 1,000 m. Fails today.
- [x] AC3: The same job runs `python -m oneframe.runtime_check`, and pyright finds no error in any
  runtime's folder or nodes, each checked with its runtime's interpreter and Python.
- [x] AC4: `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`): in a test graph of an
  image source and a node that needs an image, `--graph` measures the node at each `--set` group,
  never from the cache, the source run once, with each run's system estimate and peak. Fails today.
- [x] AC5: `test_runtime_code_is_checked_at_its_runtimes_python` (`test_runtime_check.py`): for test
  runtimes of two Pythons, their nodes and the engine's, `runtime_check` gives each its interpreter
  and Python, and names a folder whose `ruff.toml` is missing or for another Python. Fails today.
- [ ] AC6 (hardware): `npm run bench:runtime -- depth_pro` and `-- moge` on the owner's PC
  (Windows, GTX 1070) install and probe the build the plan picks.
- [ ] AC7 (hardware): `npm run bench:fit` on the same PC, on the full-size photo, which no figure
  was fitted on: every peak it measures, the device's and the system's, is at most its estimate
  (the report's ratio at most 1).

## Design

**Runtimes** follow `docs/runtimes.md`. `pyproject.toml` lists the upstream's runtime dependencies
at their newest releases with cp314 wheels on both systems (Depth Pro's `timm`, `pillow-heif`,
`numpy>=2`; MoGe's `opencv-python-headless`, `scipy`, `trimesh`, `huggingface-hub`, which `v2.py`
imports at its top, and the rest of its inference imports, not `gradio`), torch at the torch
runtime's pin and `torchvision` with it (0.29.1 in the spike). MoGe's git dependency `utils3d_moge`
is a `source` too; FlexGEMM, an extension of class `stand-in`. The spike installed the upstreams as
git packages, so these locks are new.

**`bench_fit.py`** gains `--graph <file>`, the positional argument then the key of the node to
measure. It runs what that node depends on once and measures the node, never from the cache, at each
group; the report names the graph and each input's size, and gives each row's system estimate
(`needs.system`) and peak (`peak_ram_mb`, which a card's run reports too). Its graphs,
`specs/005-first-models/bench/photo-768.json`, `photo-1536.json` and `photo-full.json`, hold the
photo at that size (its path from the repo's root) and the nodes `depth_pro`, `moge3` and `moge2`.

**Nodes.** `load(ctx)` builds the model from the upstream code with no pretrained fetch and loads
`ctx.file(...)` with `weights_only` as forced. Depth Pro casts to `ctx.precision` on the processor,
then moves to the card; MoGe's fp16 is upstream's autocast over fp32 weights. `run(ctx, model)`
writes the files of `docs/nodes.md`'s conventions. Depth Pro's network runs at a fixed 1536 px, so
its only param is `precision`, fp32 first; its camera is its own focal estimate, or the input's when
one is connected. MoGe's params are `precision` and `resolution_level`, 9 by default as upstream's,
which its `changes` lower to 5 and then 0, costing quality. MoGe-3 writes `refine_steps: 0` in its
outputs' `meta`, since the engine owns `made_with`.

**Licences** are shown, never used to block. From each README at its pin: Depth Pro's code and the
weights the pin fetches from Apple's CDN, Apple's sample-code licence (Hugging Face's copy says
`apple-amlr`); MoGe's code MIT, DINOv2's Apache-2.0. MoGe's weights': on its cards, read locally.

**Memory models** start from the spike's figures, in MB (it wrote MiB): weights on the card, Depth
Pro's 3.81 GB at fp32 and 1.91 at fp16, MoGe's 1.39 at both; working memory (the run's peak less the
weights), Depth Pro's 5.41 GB at fp32 and 3.33 at fp16, MoGe-2's 0.57, 0.91 and 1.21 at levels 0, 5
and 9. They are the models' allocations at those settings, a first estimate every machine corrects
(`learned.json`), never a choice made for that card. The local session fits them again on the 768
and 1536 px photos and AC7 checks them on the full one. A figure one card alone measured says so;
Depth Pro's fp32 on a card stays the spike's until a card that holds it reports (an RTX 5080 may,
later). MoGe's weights are fp32's at both precisions, its fp16 working memory is measured, not
halved (its own `bytes`), and `precision` is not among its `changes`; Depth Pro's `changes` hold
fp16, costing quality.

**Checks per Python.** Each runtime's folder and runtime node's folder holds a `ruff.toml` that
extends the root's and sets its runtime's Python, which the hooks format for. `pyrightconfig.json`
leaves `nodes/` and `runtimes/` out. `oneframe.runtime_check` checks every `ruff.toml`'s target and
runs pyright per installed runtime over its folder and nodes, with its interpreter and Python, and
over the engine's nodes, its configuration in the tool's scratch. `npm run engine:check` runs it on
the engine's nodes and the targets; the models job, on all of it.

**CI** (`.github/workflows/checks.yml`): a job `models (cpu)` beside `checks`, on both systems, on
every pull request, so it can be a required check, with `timeout-minutes: 45`. The weights are
cached once for both systems, keyed by the manifests' sha256s, the store at one path in the
workspace on both (4.7 GB of GitHub's 10 GB); uv's cache per system, by the locks. The photo (open
question 4) is in `engine/tests/models/` as JPEG, upright, with no metadata (a phone's photo records
where it was taken), at full size and 1536, 768 and 512 px on its long side, written by
`engine/tests/models/photos.py` from the original, which stays on the owner's PC.

**Docs:** `docs/nodes.md` (`--graph`, a node's `ruff.toml`), `docs/runtimes.md` (a stand-in for an
optional step's import), the commands (`bench:fit`, `engine:check`), the handoff's MoGe item. Risks,
caught by AC2 or AC7: the new locks; a runner's disk (the job frees its tools if it must); Depth
Pro's 8.1 GB on the processor (the spike's Linux self-test), which needs a public repository's 16 GB
runners; fp16 under the 1070's cap (the spike ran it uncapped, finding 1). Spec 016 turns `file`
params into a hash; the bench graphs and the CI script change with it.

## Tests

- Added: the three tests the criteria name (`test_model_nodes.py` and `test_runtime_check.py` new);
  the CI job and `engine/tests/models/run_on_cpu.py`, which pytest does not collect.
- Removed: none
- Renamed: none

## Verification

AC1, AC4 and AC5 run in `npm run engine:check`; AC2 and AC3 in CI's `models (cpu)` once the photo is
in; the rest in a local session on the owner's PC (Windows, GTX 1070), from the branch's root, in a
terminal from the Start menu, outside Claude desktop's package, `ONEFRAME_DATA` a new folder:

1. The photo, first (done, task 10): `uv run --project engine --frozen python
   engine/tests/models/photos.py <the original>`; look at what it wrote, and commit it.
2. AC6: `npm run bench:runtime -- depth_pro`, then `npm run bench:runtime -- moge`.
3. `npm run models:download -- depth.depth_pro`, `-- depth.moge3`, `-- depth.moge2`.
4. For each `<graph>` of `specs/005-first-models/bench/photo-768.json` and `photo-1536.json`:
   `npm run bench:fit -- --graph <graph> depth_pro --set precision=fp32 --set precision=fp16`; then
   for `moge3` and `moge2`, `npm run bench:fit -- --graph <graph> <key> --set resolution_level=0
   --set resolution_level=5 --set resolution_level=9 --set resolution_level=9 precision=fp16`.
5. Fit the figures from step 4's reports into the manifests, run step 4's commands on
   `photo-full.json` (AC7), copy every report to `specs/005-first-models/reports/`, and commit.

## Decisions taken

- Upstream code as `sources`, its dependencies listed by the runtime: neither family is on PyPI, and
  MoGe's package brings its torch index, FlexGEMM and gradio. Depth Pro on `numpy>=2`: Python 3.14
  has no numpy 1 wheels, and the spike ran 2.5.4.
- Two MoGe nodes: code and outputs differ, and ports are fixed per node; without its refinement
  MoGe-3 is MoGe-2's size and speed. One `moge` runtime: one upstream.
- `bench:fit --graph`, not a flag that builds an input: a graph is how a node gets real inputs.
- CI runs the real weights, not random ones, so it checks the files and the loading.
- The spike's figures first, AC7's after: the nodes ship with real numbers, and the fit is held to
  torch's reserve, which `bench:fit` reads and the spike, reading allocated memory, did not.
- Figures fitted at 768 and 1536 px and checked at full size, one-sided: a check on the runs the
  figures came from fails only by noise, and an estimate above a peak wastes memory, never a run.
- Depth Pro casts before the card: casting on a card holds both copies at once (finding 2).
- MoGe's fp16 is the person's choice, never the fit's: autocast keeps its weights at fp32, so it
  shrinks no weights; whether it is faster depends on the card. MoGe's default level is upstream's.
- Depths between 0 and 1,000 m, not 0.1 to 100: MoGe reached 146 m on the spike's street, and
  random weights give Depth Pro's 10,000 m, which the bound still catches.
- pyright per runtime from the definitions, ruff by a `ruff.toml` per folder that `runtime_check`
  holds: nothing outside a folder names its node, and the hooks format for its Python.
- The photo as JPEG: a full-size PNG would be tens of MB in git.
- fp16 needs no compute capability: torch runs half precision on every card its builds support.
- Depth Pro scales a connected camera to the photo; one of another shape is not this photo's.

## Out of scope

- The page, graphs and results: 015 to 017. Keeping models loaded: spec 004. MoGe-3 ViT-G, other
  depth families: a node each, later. MoGe-3's refinement: with a report from a compute 8.0 card.

## Open questions (answered by the owner)

1. **MoGe-3 without its refinement** (2026-11-04): ship it, labelled, beside MoGe-2; the graph uses
   it.
2. **The weights' licences** (2026-11-04): ship both, the licences on the node's card. Found since:
   Depth Pro's pinned weights are under its code's licence (Design).
3. **Depth Pro on a card too small for fp32** (2026-11-05): keep spec 002's rule and fp32 first, the
   shipped graph (015) leaving `precision` unset; and nothing designed around the 1070 (req. 9).
4. **The photo CI and the bench read** (2026-11-05): the owner's own, a plane in a museum with
   nobody in it, given under the repo's licence; a local session puts it in (Verification).
