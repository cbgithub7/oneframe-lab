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
  *Pick up here (2026-11-05): task 4. Tasks 1 to 3 are on `claude/eloquent-archimedes-74uqi8`,
  stacked on PR #58; task 10, the photo, can run now (the handoff's "Waiting on a local session").*
- [ ] 4. **The `depth_pro` runtime** (requirement 1): `runtimes/depth_pro/` with its builds as the
  torch runtime's (`cu130`, `cu126`, `cpu`), upstream at `9e65e4d` as a pinned `source`, its
  dependencies listed (`numpy>=2`), `torchvision` beside torch, `uv.lock`, `ruff.toml`.
- [ ] 5. **The `moge` runtime** (requirements 1 and 4): `runtimes/moge/` the same way, upstream at
  `74fbce0` and `utils3d_moge` as pinned `sources`, `huggingface-hub` among its dependencies and no
  `gradio`, the `flex_gemm` stand-in (imports, raises on use), `uv.lock`, `ruff.toml`.
- [ ] 6. **The three nodes** (requirements 2, 3, 6, 9): `nodes/depth.depth_pro/`,
  `nodes/depth.moge3/` and `nodes/depth.moge2/`, each a `node.json` (the ports with their facets,
  the params, the files from `specs/003-model-store/live/`, the memory model from the spike's
  figures in MB, naming the card, the licences), `node.py` (`load` and `run`) and `ruff.toml`.
  `test_the_model_nodes_fit_a_range_of_machines` (AC1).
- [ ] 7. **CI** (AC2, AC3): `engine/tests/models/run_on_cpu.py` (every node of the runtimes it
  installed, fed the 512 px photo; each run `done`; valid depths above 0 and below 1,000 m) and the
  job `models (cpu)` in `.github/workflows/checks.yml`, which also runs `runtime_check` over every
  runtime. The bench graphs `specs/005-first-models/bench/photo-768.json`, `photo-1536.json` and
  `photo-full.json`.
- [ ] 8. **Docs:** `docs/nodes.md` (a node's `ruff.toml`), `docs/runtimes.md` (a stand-in for an
  optional step's import; a runtime's `ruff.toml`), the handoff's MoGe item, and "Waiting on a local
  session" (tasks 10 and 11, with their commands and reports).
- [ ] 9. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC7 one by
  one, AC6 and AC7 pending hardware.
- [ ] 10. **A local session: the photo** (Verification, step 1), committed to the branch. CI's
  `models (cpu)` job cannot pass without it.
- [ ] 11. **A local session: AC6 and AC7** (Verification, steps 2 to 5), the figures fitted into the
  manifests and the reports under `specs/005-first-models/reports/`.
