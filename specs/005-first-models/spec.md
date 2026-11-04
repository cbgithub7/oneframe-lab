# 005: The first model nodes: Depth Pro and MoGe

Status: draft, cut to what its spike found (2026-11-04), for the owner's approval
Owner approval: (date, once approved)

The first of four specs for step 3, first light: this one, then [015](../015-graphs/spec.md)
(graphs as data), [016](../016-door/spec.md) (photos and results through the door) and
[017](../017-first-page/spec.md) (the page). It needs none of them. It starts from the pins in
`specs/003-model-store/live/`, the [review of 2026-10-12](../../docs/reviews/2026-10-12.md) (5.8),
the [review of 2026-11-04](../../docs/reviews/2026-11-04.md) (2.7), and its spike on the owner's
GTX 1070 on Windows ([report](reports/2026-11-03-spike.md); "finding N" below is its).

## Problem

No node runs a model. The spike showed that real ones can, on Windows and on the 1070: both
families install from their GitHub code with Python 3.14 and torch 2.14.0, no FlexGEMM, gradio or
upstream torch index; Depth Pro runs on numpy 2; MoGe-3 runs without its refinement; the weights
load from the store with `weights_only` forced and connections refused. It also found Depth Pro at
fp32 too big for an 8 GB card, its fp16 load peaking at fp32's size, and MoGe's fp16 saving nothing.
`bench:fit` cannot measure a node that needs an image, and runtime code is linted for 3.15 not 3.14.

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
6. **Memory models from measurement,** each figure naming its report: weights per precision,
   working memory over the settings and the photo's size, and system memory. MoGe's fp16 never
   counts as a saving.
7. **CI runs each node on the processor** with real torch and weights, on Windows and Ubuntu.
8. **Runtime code is checked at its runtime's Python:** a runtime's folder and its nodes, by ruff
   for that Python and by pyright with that runtime's interpreter and packages. Nothing outside a
   node's folder names the node.

## Acceptance criteria

- [ ] AC1: `test_the_model_nodes_fit_a_range_of_machines` (`test_model_nodes.py`): fitted from their
  manifests on no card, an 8 GB card and a 24 GB card, each node lands where its figures say: Depth
  Pro at its defaults on the 8 GB machine's processor, with fp16 on the card as the faster way, and
  on the 24 GB card at fp32; MoGe on either card at every level.
- [ ] AC2: The CI job `models (cpu)`, on Windows and Ubuntu, installs every runtime's `cpu` build,
  downloads the nodes' files with `npm run models:download` (cached by sha256), and runs
  `engine/tests/models/run_on_cpu.py`, which feeds the 512 px photo to every node of those
  runtimes: each run ends `done` (the scheduler checks every output against its port), and every
  valid depth lies above 0 and below 1,000 m. Fails today: no such nodes.
- [ ] AC3: The same job runs `python -m oneframe.runtime_check`, and pyright finds no error in any
  runtime's folder or nodes, each checked with its runtime's interpreter and Python.
- [ ] AC4: `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`): in a test graph of an
  image source and a node that needs an image, `--graph` measures the node at each `--set` group,
  never from the cache, the source run once, with each run's system estimate and peak. Fails today.
- [ ] AC5: `test_runtime_code_is_checked_at_its_runtimes_python` (`test_runtime_check.py`): for test
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
is a `source` too. FlexGEMM is an extension of class `stand-in`. The spike installed the upstreams
as git packages, so these locks are new.

**`bench_fit.py`** gains `--graph <file>`, the positional argument then the key of the node to
measure. It runs what that node depends on once and measures the node, never from the cache, at
each group; the report names the graph and each input's size, and gives each row's system estimate
(the fit's `needs.system`) and peak (`peak_ram_mb`, which a card's run reports too). Its graphs,
`specs/005-first-models/bench/photo-768.json`, `photo-1536.json` and `photo-full.json`, hold the
photo at that size (its path from the repo's root) and the nodes `depth_pro`, `moge3` and `moge2`.

**Nodes.** `load(ctx)` builds the model from the upstream code with no pretrained fetch and loads
`ctx.file(...)` with `weights_only` as forced. Depth Pro casts to `ctx.precision` on the processor,
then moves to the card; MoGe's fp16 is upstream's autocast over fp32 weights. `run(ctx, model)`
writes the files of `docs/nodes.md`'s conventions. Depth Pro's network runs at a fixed 1536 px, so
its only param is `precision`, fp32 first (open question 3); its camera is its own focal estimate,
or the input's when one is connected. MoGe's params are `precision` and `resolution_level`, 9 by
default as upstream's, which its `changes` lower to 5 and then 0, costing quality. MoGe-3 writes
`refine_steps: 0` in each output's `meta` (the engine owns `made_with`).

**Licences** are shown, never used to block. From each README at its pin: Depth Pro's code and the
weights the pin fetches from Apple's CDN, Apple's sample-code licence (Hugging Face's copy says
`apple-amlr`); MoGe's code MIT, DINOv2's Apache-2.0. MoGe's weights': on its cards, read locally.

**Memory models** start from the spike's figures, in MB (it wrote MiB): weights on the card, Depth
Pro's 3.81 GB at fp32 and 1.91 at fp16, MoGe's 1.39 at both; working memory (the run's peak less the
weights), Depth Pro's 5.41 GB at fp32 and 3.33 at fp16, MoGe-2's 0.57, 0.91 and 1.21 at levels 0, 5
and 9. The local session fits them again on the 768 and 1536 px photos and AC7 checks them on the
full one; Depth Pro's fp32 on a card stays the spike's, its only card run. MoGe's fp16 states its
own `bytes`, so it is not halved, and is not in its `changes`; Depth Pro's hold fp16, costing
quality. Depth Pro at fp32 needs 9.2 GB on a card; where it does not fit, spec 002's decision 7 runs
it on the processor, about 99 s on the spike's i7-4770K, and with `precision` unset `node.fit`
offers fp16 on the card (6 s in the spike).

**Checks per Python.** Each runtime's folder and runtime node's folder holds a `ruff.toml` that
extends the root's and sets its runtime's Python, which the hooks format for. `pyrightconfig.json`
leaves `nodes/` and `runtimes/` out (it names `runtimes/torch` today). `oneframe.runtime_check`
checks every `ruff.toml`'s target and runs pyright per installed runtime over its folder and nodes,
with its interpreter and Python, and over the engine's nodes, its configuration in the tool's
scratch. `npm run engine:check` runs it on the engine's nodes and the targets; the models job, on
all of it.

**CI** (`.github/workflows/checks.yml`): a job `models (cpu)` beside `checks`, on both systems, on
every pull request, so it can be a required check, with `timeout-minutes: 45` (on the spike's
processor, Depth Pro about 4 minutes a process, MoGe under 1). The weights are cached once for both
systems, keyed by the manifests' sha256s, the store at one path in the workspace on both (4.7 GB of
GitHub's 10 GB); uv's cache per system, by the locks.

**Docs:** `docs/nodes.md` (`--graph`, a node's `ruff.toml`), `docs/runtimes.md` (a stand-in for an
optional step's import), the commands (`bench:fit`, `engine:check`), the handoff's MoGe item.

Risks, caught by AC2 or AC7: the new locks; a runner's disk (the job frees its tools if it must);
Depth Pro's 8.1 GB on the processor (the spike's Linux self-test), which needs a public repository's
16 GB runners; fp16 under the 1070's cap (the spike ran it uncapped, finding 1). Spec 016 turns
`file` params into a hash; the bench graphs and the CI script change with it.

## Tests

- Added: `test_the_model_nodes_fit_a_range_of_machines` (`test_model_nodes.py`, new);
  `test_runtime_code_is_checked_at_its_runtimes_python` (`test_runtime_check.py`, new);
  `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`); the CI job and its script
  `engine/tests/models/run_on_cpu.py`, which pytest does not collect.
- Removed: none
- Renamed: none

## Verification

The spike's facts are this spec's; nothing it measured is claimed for the nodes. AC1, AC4 and AC5
run in `npm run engine:check`; AC2 and AC3 in CI's `models (cpu)`. AC6 and AC7, in a local session
on the owner's PC (Windows, GTX 1070), from the branch's root, in a terminal opened from the Start
menu, outside Claude desktop's package, with `ONEFRAME_DATA` at a new empty folder:

1. `npm run bench:runtime -- depth_pro`, then `npm run bench:runtime -- moge`.
2. `npm run models:download -- depth.depth_pro`, `-- depth.moge3`, `-- depth.moge2`.
3. For each `<graph>` of `specs/005-first-models/bench/photo-768.json` and `photo-1536.json`:
   `npm run bench:fit -- --graph <graph> depth_pro --set precision=fp32 --set precision=fp16`; then
   for `moge3` and `moge2`, `npm run bench:fit -- --graph <graph> <key> --set resolution_level=0
   --set resolution_level=5 --set resolution_level=9 --set resolution_level=9 precision=fp16`.
4. Fit the figures from step 3's reports into the manifests, run step 3's commands on
   `photo-full.json` (AC7), copy every report to `specs/005-first-models/reports/`, and commit.

## Decisions taken

- Upstream code as `sources`, its dependencies listed by the runtime: neither family is on PyPI,
  and MoGe's package would bring its torch index, FlexGEMM and gradio into every build. Depth Pro
  on `numpy>=2`: Python 3.14 has no numpy 1 wheels, and the spike ran it on 2.5.4.
- Two MoGe nodes, not one with a choice: code and outputs differ, and ports are fixed per node;
  without its refinement MoGe-3 is MoGe-2's size and speed. One `moge` runtime: one upstream. No
  FlexGEMM build: nothing here can test one.
- `bench:fit --graph`, not a flag that builds an input: a graph is how a node gets real inputs.
- CI runs the real weights, not random ones, so it checks the files and the loading; on every pull
  request, with 45 minutes, several times the spike's installs and processor runs together.
- The spike's figures first, AC7's after: the nodes ship with real numbers, and the fit is held to
  torch's reserve, which `bench:fit` reads and the spike, reading allocated memory, did not.
- Figures fitted at 768 and 1536 px and checked at full size, one-sided: a check on the runs the
  figures came from fails only by noise, and an estimate above a peak wastes memory, never a run.
- Depth Pro casts before the card: cast on it, its fp16 load peaked at fp32's 3.8 GB (finding 2).
- MoGe's fp16 is the person's choice, never the fit's: it saved nothing on compute 6.1 (finding 3),
  and may still be faster on cards with fast fp16. MoGe's default level is upstream's.
- Depths between 0 and 1,000 m, not 0.1 to 100: MoGe reached 146 m on the spike's street, and
  random weights give Depth Pro's 10,000 m, which the bound still catches.
- pyright per runtime from the definitions, ruff by a `ruff.toml` per folder that `runtime_check`
  holds: nothing outside a folder names its node, and the hooks format for its Python.

## Out of scope

- The page, graphs and results: 015 to 017. Keeping models loaded: spec 004. MoGe-3 ViT-G, other
  depth families: a node each, later. MoGe-3's refinement: with a report from a compute 8.0 card.

## Open questions

1. **MoGe-3 without its refinement:** answered (the owner, 2026-11-04): ship it so, labelled, beside
   MoGe-2, and the shipped graph uses it.
2. **The weights' licences:** answered (the owner, 2026-11-04): ship both, the licences on the
   node's card. Found since: Depth Pro's pinned weights are under its code's licence (Design).
3. **Depth Pro on a card too small for fp32,** as the 1070: spec 002's rule runs it on the
   processor, about 99 s, and offers fp16 on the card, about 6 s. Recommended: keep the rule, fp32
   first, and the shipped graph (015) leaves `precision` unset. Or fp16 first, as upstream's CLI
   loads it; a machine with no card then changes it to fp32, reported as costing quality.
4. **The photo CI and the bench read,** in `engine/tests/models/` beside the app's MIT code.
   Recommended: one the owner takes and gives under the repo's licence, nobody in it, about 12 MP,
   with 1536, 768 and 512 px copies. Or a CC0 photo from Wikimedia Commons, its page beside it.
