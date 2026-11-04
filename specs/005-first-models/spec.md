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

No node runs a model. The spike showed that real ones can: both families install on Windows from
their GitHub code with Python 3.14 and torch 2.14.0, with no FlexGEMM, gradio or upstream torch
index; Depth Pro runs on numpy 2, against upstream's `numpy<2`; MoGe-3 runs without its refinement
beside a stand-in for `flex_gemm`; the weights load from the store with `weights_only` forced and
connections refused. It also found that Depth Pro at fp32 needs more than an 8 GB card, its fp16
load peaked at fp32's size, and MoGe's fp16 saves no memory. `bench:fit` cannot measure a node that
needs an image, and node code is linted for Python 3.15, not its runtime's 3.14.

## Requirements

1. **Two runtimes, one per family:** `runtimes/depth_pro/` and `runtimes/moge/`, on the Python and
   torch of `runtimes/torch`, with the upstream code as pinned `sources` and its dependencies listed
   by the runtime, so no upstream index pin or optional package enters the lock.
2. **Three nodes:** `depth.depth_pro` (an image and an optional camera → metric z-depth and the
   camera it used), `depth.moge3` (MoGe-3 ViT-L) and `depth.moge2` (MoGe-2 ViT-L with normals),
   each MoGe an image → a metric camera-frame point map, metric z-depth, a valid mask, the camera
   and normals. Ports say exactly what each value is.
3. **Each node loads once, then runs** (`load` and `run`), on `cuda` or `cpu`, at `fp32` or `fp16`,
   reading its weights only through `ctx.file`. An fp16 load never holds fp32 weights on the card.
4. **No build installs FlexGEMM yet.** Every `moge` build carries a stand-in for `flex_gemm` that
   imports and raises on use; MoGe-3 runs without its refinement and says so in its outputs'
   `meta` (`refine_steps: 0`) until a card of compute 8.0 or newer reports.
5. **`bench:fit` measures a node inside a graph** (`--graph <file> <key>`), so its inputs are real,
   and gives each run's system memory peak beside the card's; without `--graph`, as today.
6. **Memory models from measurement:** weights per precision, working memory over the settings and
   the photo's size, and system memory, from `bench:fit` on a real card, each figure naming its
   report; `null`, with a source saying so, until measured. MoGe's fp16 never counts as a saving.
7. **CI runs each node on the processor** with real torch and weights, on Windows and Ubuntu.
8. **Each node is checked at its runtime's Python:** ruff for that Python, pyright with that
   runtime's interpreter and packages. Nothing outside the node's folder names it.

## Acceptance criteria

- [ ] AC1: `test_model_nodes_are_well_formed` (`test_model_nodes.py`): the registry and the runtime
  manager read the three manifests and two definitions with no problem.
- [ ] AC2: The CI job `models (cpu)`, on Windows and Ubuntu, installs both runtimes' `cpu` builds,
  downloads the nodes' files with `npm run models:download` (cached by sha256), and runs
  `engine/tests/models/run_on_cpu.py`: each node through the scheduler on the 512 px photo, each
  output checked against its port (type, facets, shape, finite where valid, depths above 0 and
  below 1,000 m, `trust` `predicted`). Fails today: no such nodes.
- [ ] AC3: The same job runs `python -m oneframe.node_check` over the runtimes it installed, and
  pyright finds no error in their nodes, each checked with its runtime's interpreter and Python.
- [ ] AC4: `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`): given a test graph of
  an image source and a test node that needs an image, `--graph` measures the test node at each
  `--set` group from an empty cache, the source cached, with each run's system peak. Fails today.
- [ ] AC5: `test_each_node_is_checked_at_its_runtimes_python` (`test_node_check.py`): with test
  nodes in the engine and in two runtimes of different Pythons, `node_check` gives each group its
  interpreter and Python, and names a runtime node whose `ruff.toml` is missing or targets another
  Python. Fails today.
- [ ] AC6 (hardware): `npm run bench:runtime -- depth_pro` and `-- moge` on the owner's PC
  (Windows, GTX 1070) install and probe the build the plan picks; the reports give each install's
  seconds and disk.
- [ ] AC7 (hardware): `npm run bench:fit` for each node on the same PC, at the settings below,
  finishes with every measured peak, the device's and the system's, at most its estimate once the
  manifests hold the figures it measured; the report gives seconds, peaks, fits, where Depth Pro
  at fp32 ran, and how MoGe-3 ran.

## Design

**Runtimes** follow `docs/runtimes.md`. `pyproject.toml` lists the upstream's runtime dependencies
at their newest releases with cp314 wheels on both systems (Depth Pro's `timm`, `pillow-heif`,
`numpy>=2`; MoGe's `opencv-python-headless`, `scipy`, `trimesh`, `utils3d` and the rest it imports
for inference, not `gradio`, `huggingface-hub` or its CLI's), with torch and `torchvision` at
`runtimes/torch`'s pins; the spike's locks resolved so (80 and 88 packages). A git dependency of the
upstream (`utils3d_moge`) is a pinned `source` too. FlexGEMM is an extension of class `stand-in`: a
`flex_gemm` package in the runtime's folder with the names `v3.py` imports.

**`bench_fit.py`** gains `--graph <file> <key>`: it reads the graph with `Graph.from_json`, runs
what `<key>` depends on once, cached, and measures `<key>` from an empty cache at each group. The
report names the graph and each input's size, and gives each row's `peak_ram_mb`, which a card's
run reports too. Nothing in the bench names a node. Its graphs, `specs/005-first-models/bench/`
`photo-1536.json` and `photo-768.json`, hold the photo at that size and all three nodes.

**Nodes.** `load(ctx)` builds the model from the upstream code with no pretrained fetch and loads
`ctx.file(...)` with `weights_only` as forced. Depth Pro casts to `ctx.precision` on the processor,
then moves to the card; MoGe's fp16 is upstream's autocast over fp32 weights. `run(ctx, model)`
reads the PNG, infers, and writes the `.npz` and `.json` of `docs/nodes.md`'s conventions. Depth
Pro's network runs at a fixed 1536 px, so its only param is `precision`, fp32 first; its camera is
its own focal estimate (`source=estimated`), or the input's when one is connected. MoGe's params are
`resolution_level` and `precision`; its masked pixels (`inf`) are `valid=False`, its intrinsics
turned from fractions to pixels; MoGe-3 writes `refine_steps: 0` in each output's `meta` (the
engine owns `made_with`).

**Licences** are shown, never used to block. The code's, from each README at its pin: Depth Pro's
Apple sample-code licence, MoGe's MIT, DINOv2's Apache-2.0. Depth Pro's README at `9e65e4d` puts the
weights under that same licence, and the pin is that copy, from Apple's CDN (Hugging Face's copy
says `apple-amlr`). MoGe's weights' licence is on their model cards, which the local session reads.

**Memory models** start with weights from the pinned sizes and the rest `null`; AC7's report fills
them, in the PR that commits it. Depth Pro's working memory is mostly fixed by its 1536 px network,
which two photo sizes show; MoGe's grows with `resolution_level` (0.55, 0.87 and 1.16 GB allocated
at levels 0, 5 and 9 in the spike) and with the photo. MoGe's fp16 states its own `bytes`, so its
working memory is not halved, and is not among its `changes`; Depth Pro's `changes` hold fp16,
costing quality. Depth Pro at fp32 allocated 8.8 GB on the 1070; where it does not fit and the
processor has room, spec 002's decision 7 runs it there, about 99 s on the spike's i7-4770K, and
`node.fit` offers fp16 on the card, about 6 s (open question 3). The cap keeps a fit honest on
Windows, where the spike, uncapped, overran the card unrefused (finding 1).

**Checks per runtime.** A runtime node's folder holds a `ruff.toml` that extends the root's and
sets its runtime's Python, which the hooks then format for. `pyrightconfig.json` leaves `nodes/`
out; `oneframe.node_check` reads the manifests and runs pyright once per runtime over its nodes,
with its interpreter and Python, and once over the engine's nodes, its configuration under the data
root. `npm run engine:check` runs it for the engine's nodes and every `ruff.toml`; the models job
for its runtimes.

**CI** (`.github/workflows/checks.yml`): a job `models (cpu)` beside `checks`, on both systems, on
every pull request, so it can be a required check, with `timeout-minutes: 45` (on the spike's
processor, Depth Pro about 4 minutes a process, MoGe under 1). The weights are cached once for both
systems (`enableCrossOsArchive`, keyed by the manifests' sha256s, 4.7 GB of GitHub's 10 GB), uv's
cache per system by the locks.

**Docs:** `docs/nodes.md` (`--graph`, a node's `ruff.toml`), `docs/runtimes.md` (a stand-in for an
optional step's import), the commands' `bench:fit` line, the handoff's MoGe item.

Risks: a runner's disk too small (the job frees its preinstalled tools if it must); Depth Pro's
8.1 GB on the processor (the spike's self-test), which needs the 16 GB runners a public repository
gets; the processor's peak on Windows, unread by the spike (finding 4), read in AC7 by the engine.

## Tests

- Added: `test_model_nodes_are_well_formed` (`test_model_nodes.py`, new);
  `test_each_node_is_checked_at_its_runtimes_python` (`test_node_check.py`, new);
  `test_bench_fit_measures_a_node_in_a_graph` (`test_bench_fit.py`); the CI job and its script
  `engine/tests/models/run_on_cpu.py`, which pytest does not collect.
- Removed: none
- Renamed: none

## Verification

**The spike passed** on 2026-11-03 ([report](reports/2026-11-03-spike.md)). Its facts are this
spec's; nothing it measured is claimed for the nodes, which AC6 and AC7 measure.

AC1, AC4 and AC5 run in `npm run engine:check`; AC2 and AC3 in CI's `models (cpu)`. AC6 and AC7, in
a local session on the owner's PC (Windows, GTX 1070), from the branch, in a terminal opened from
the Start menu, outside Claude desktop's package, with `ONEFRAME_DATA` at a new empty folder:

1. `npm run bench:runtime -- depth_pro`, then `npm run bench:runtime -- moge`.
2. `npm run models:download -- depth.depth_pro`, `-- depth.moge3`, `-- depth.moge2`.
3. For each `<graph>` of `specs/005-first-models/bench/photo-1536.json` and `photo-768.json`:
   `npm run bench:fit -- --graph <graph> depth_pro --set precision=fp32 --set precision=fp16`; then
   for `moge3` and `moge2`, `npm run bench:fit -- --graph <graph> <node> --set resolution_level=0
   --set resolution_level=5 --set resolution_level=9 --set resolution_level=9 precision=fp16`.
4. Copy the reports to `specs/005-first-models/reports/`, write the figures into the manifests,
   run step 3 again to show every peak within its estimate, and commit.

## Decisions taken

- Upstream code as `sources`, its dependencies listed by the runtime: neither family is on PyPI,
  and MoGe's package would bring its torch index, FlexGEMM and gradio into every build. Depth Pro
  on `numpy>=2`: Python 3.14 has no numpy 1 wheels, and the spike ran it on 2.5.4.
- Two MoGe nodes, not one with a choice: code and outputs differ, and ports are fixed per node;
  without its refinement MoGe-3 is MoGe-2's size and speed. One `moge` runtime: one upstream.
- No FlexGEMM build until a card that can run it reports: nothing here could test one.
- `bench:fit --graph`, not a flag that builds an input: a graph is how a node gets real inputs.
- CI runs the real weights, not random ones, so it checks the files and the loading.
- The manifests' figures from AC7, not the spike: the spike read allocated memory in its own
  harness, and the fit is held to torch's reserve, which `bench:fit` reads.
- Depth Pro casts on the processor before the card: cast on the card, its fp16 load peaked at
  fp32's 3.6 GB (finding 2).
- MoGe's fp16 is the person's choice, never the fit's: it saved nothing on compute 6.1 (finding 3),
  and may still be faster on cards with fast fp16.
- Depths between 0 and 1,000 m, not 0.1 to 100: MoGe reached 146 m on the spike's street, and
  random weights give Depth Pro's 10,000 m, which the bound still catches.
- pyright per runtime from the manifests, ruff by a `ruff.toml` in the node's folder that
  `node_check` holds: nothing outside the folder names the node; the hooks format for its Python.

## Out of scope

- The page, graphs and results: 015 to 017. Keeping models loaded: spec 004. MoGe-3 ViT-G, other
  depth families: a node each, later. MoGe-3's refinement: with a report from a compute 8.0 card.

## Open questions

1. **MoGe-3 without its refinement:** answered (the owner, 2026-11-04): ship it so, labelled, beside
   MoGe-2, and the shipped graph uses it.
2. **The weights' licences:** answered (the owner, 2026-11-04): ship both, the licences on the
   node's card. Found since: Depth Pro's pinned weights are under its code's licence (Design).
3. **Depth Pro on a card too small for fp32,** as the 1070: spec 002's rule runs it on the
   processor, about 99 s, and offers fp16 on the card, about 6 s. Recommended: keep the rule and
   fp32 first; the shipped graph (015) leaves `precision` unset, to fit every machine. The other
   choice: fp16 first, as upstream's CLI loads it; a machine with no card then changes it to fp32,
   which the fit would report as costing quality.
4. **The photo CI and the bench read,** in `engine/tests/models/` beside the app's MIT code.
   Recommended: one the owner takes and gives under the repo's licence, nobody in it, at 1536, 768
   and 512 px. The other choice: a CC0 photo from Wikimedia Commons, its page named beside it.
