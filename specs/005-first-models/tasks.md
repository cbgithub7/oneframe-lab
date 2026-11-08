# 005: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. **The photo script:** `engine/tests/models/photos.py <original>` writes `photo-full.jpg`,
  `photo-1536.jpg`, `photo-768.jpg` and `photo-512.jpg` beside itself (the long side at that size),
  upright, RGB, with no metadata; a test of it on a made photo with EXIF orientation and GPS. Then a
  local session can put the owner's photo in at any time (task 10).
- [x] 2. **`bench:fit --graph <file>`** (AC4): the positional argument is then the key of the node
  to measure; what it depends on runs once, the node never from the cache; the report names the
  graph and each input's size, and every row gives the system estimate (`needs.system`) and peak
  (`peak_ram_mb`). `test_bench_fit_measures_a_node_in_a_graph`. Update the `bench:fit` section of
  `docs/nodes.md` and the command's line in `AGENTS.md`.
- [x] 3. **Checks per Python** (AC5): `oneframe.runtime_check` reads the runtime definitions and the
  node manifests, checks each runtime folder's and runtime node folder's `ruff.toml` (it extends the
  root's; its target is the runtime's Python), and runs pyright per installed runtime over its
  folder and nodes with its interpreter and Python, and over the engine's nodes, its configuration
  in the tool's scratch. `runtimes/torch/ruff.toml`; `pyrightconfig.json` leaves `nodes/` and
  `runtimes/` out; `npm run engine:check` runs it.
  `test_runtime_code_is_checked_at_its_runtimes_python`.
- [x] 4. **The `depth_pro` runtime** (requirement 1): `runtimes/depth_pro/` with its builds as the
  torch runtime's (`cu130`, `cu126`, `cpu`), upstream at `9e65e4d` as a pinned `source`, its
  dependencies listed (`numpy>=2`), `torchvision` beside torch, `uv.lock`, `ruff.toml`.
- [x] 5. **The `moge` runtime** (requirements 1 and 4): `runtimes/moge/` the same way, upstream at
  `74fbce0` and `utils3d_moge` as pinned `sources`, `huggingface-hub` among its dependencies and no
  `gradio`, the `flex_gemm` stand-in (imports, raises on use), `uv.lock`, `ruff.toml`.
  *What the session before task 6 worked out for it (2026-11-05):*
    - *Figures in MB from the spike's MiB (the spec's Design): Depth Pro working `1248 + 1041 x
      bytes` (5,412 at fp32, 3,330 at fp16); MoGe working `bytes x (a + b x resolution_level)`,
      MoGe-2 a 143.4 and b 17.74, MoGe-3 a 160.4 and b 16.28, fp16's `bytes` 4.68 (both: the spike's
      level 9 at fp16). No pixels term yet: AC7 fits one.*
    - *Depth Pro's load: build under `torch.device("meta")`, `load_state_dict(..., assign=True)`
      from its fp16 file, cast on the processor, then move to `ctx.device`: no fp32 copy on a card.
      Its own `infer` resizes to 1536 px and back.*
    - *MoGe: `MoGeModel.from_pretrained(ctx.file(...))`, MoGe-3 with `model_kwargs={"refiner":
      None}` and `refine_steps=0`; `infer(image, resolution_level=..., use_fp16=...)` gives points,
      depth (inf where masked), mask, normalised intrinsics and normal.*
    - *`ctx.output` takes no trust: Depth Pro's camera is declared `predicted`, its `source` facet
      set at run time (`estimated`, or the input camera's).*
    - *Here the `cpu` builds install from `git archive` copies put in
      `<root>/runtimes/<id>/downloads/<sha256>` (docs/runtimes.md). Apple's CDN and Hugging Face
      are unreachable from a cloud session: the nodes run here with random weights only. The spike
      shows how to build each with none (`spike/run_model.py`, `spike/moge/configs/`); a test
      checkpoint for `from_pretrained` is `{"model_config": ..., "model": state_dict}`.*
    - *The work stays on this branch, and the PR (task 9) goes from it to `main` (PR #58 is merged).
      For the PR: `runtime:check` is its own npm script, which `engine:check` runs (a test
      holds every engine tool to `scripts/tool.js`); the spec says `engine:check` runs it.*
- [x] 6. **The three nodes** (requirements 2, 3, 6, 9): `nodes/depth.depth_pro/`,
  `nodes/depth.moge3/` and `nodes/depth.moge2/`, each a `node.json` (the ports with their facets,
  the params, the files from `specs/003-model-store/live/`, the memory model from the spike's
  figures in MB, naming the card, the licences), `node.py` (`load` and `run`) and `ruff.toml`.
  `test_the_model_nodes_fit_a_range_of_machines` (AC1).
  *Done 2026-11-05: each ran `done` through `bench:fit --graph` on a cloud processor with random
  weights of the real shapes ([report](reports/2026-11-05-cloud-cpu.md)), which also set MoGe's
  system figure; `runtime:check` passes with both runtimes installed.*
- [x] 7. **CI** (AC2, AC3): `engine/tests/models/run_on_cpu.py` (every node of the runtimes it
  installed, fed the 512 px photo; each run `done`; valid depths above 0 and below 1,000 m) and the
  job `models (cpu)` in `.github/workflows/checks.yml`, which also runs `runtime_check` over every
  runtime. The bench graphs `specs/005-first-models/bench/photo-768.json`, `photo-1536.json` and
  `photo-full.json`.
  *Done 2026-11-05, unverified until CI runs it on the PR: here `run_on_cpu.py` passed MoGe-2 and
  MoGe-3 and failed Depth Pro on random weights (10,000 m, which the bound catches). The job loops
  over `nodes/*/` and `runtimes/*/` with the existing commands, and `run_on_cpu.py` finds the photo's
  node by its ports, so nothing names a node or a runtime.*
- [x] 8. **Docs:** `docs/nodes.md` (a node's `ruff.toml`), `docs/runtimes.md` (a stand-in for an
  optional step's import; a runtime's `ruff.toml`), the handoff's MoGe item, and "Waiting on a local
  session" (tasks 10 and 11, with their commands and reports).
  *Done 2026-11-05: task 10 was already done, so only task 11 waits there.*
- [x] 9. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC7 one by
  one, AC6 and AC7 pending hardware.
  *Done 2026-11-06: 13 findings, 11 fixed and 2 answered in the PR,
  [PR #60](https://github.com/cbgithub7/oneframe-lab/pull/60). Its `models (cpu)` job passed AC2
  and AC3 with the real weights on Windows and Ubuntu ([run](https://github.com/cbgithub7/oneframe-lab/actions/runs/42309103039)). Pick up here: task 11, in a
  local session on a new branch from `main`, whose PR marks the spec done.*
- [x] 10. **A local session: the photo** (Verification, step 1), committed to the branch. CI's
  `models (cpu)` job cannot pass without it.
- [x] 11. **A local session: AC6 and AC7** (Verification, steps 2 to 5), the figures fitted into the
  manifests and the reports under `specs/005-first-models/reports/`. Also the licence on each MoGe
  weights' card (`links.weights` in `nodes/depth.moge2/` and `nodes/depth.moge3/`), which a cloud
  session cannot reach, written into the manifests' `licence.weights`.
  *Done 2026-11-06 on the GTX 1070, outside Claude desktop's package: both passed
  ([AC6](reports/2026-11-06-ac6.md), [AC7](reports/2026-11-06-ac7.md)); both licences MIT; a 10 GB
  card among the test machines, with the owner's go-ahead, so AC1's test still reaches every
  branch. The AC7 report's findings (MoGe's peak is a larger-of, bench:fit's report names) are
  for a cloud session.*
