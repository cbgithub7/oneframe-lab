# 002: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

Pick up at task 6 (plan approved 2026-10-01).

- [x] 1. **Machine profiles and system memory.** `hardware.py` reads total and available system
  memory (on Windows, the smaller of physical and commit), through a reader passed in;
  `engine/tests/fixtures/machines.py` holds the profiles. Tests for both.
- [x] 2. **The memory model in manifests.** Parse and check `memory` (precisions and where they
  run, weights by precision and checkpoint, working and system formulas, outside torch, changes,
  upgrades, time, sources); add `precision`; `to_json` shows it. AC1 tests.
- [x] 3. **The estimate, the margins, the settings and the fit.** `memory.estimate()`,
  `settings.json`, `memory.fit()` with upgrades, speed before quality, the slow warning and its
  alternative, tried anyway, `never_reduce_quality`, `fit: "off"`; `Step.explicit` in `graph.py`.
  The test table and every extra case, and the renamed run (AC2).
- [x] 4. **What each machine learns.** The store in `memory.py`: corrections from recent runs,
  peaks for unknown estimates, seconds, the key's parts, forget, the bound, atomic writes. AC4
  tests.
- [x] 5. **The child.** First, `ruff.toml` and `pyrightconfig.json` target Python 3.11 for code
  that runs in runtimes (plan decision 13). Then the cap after the context exists, with and
  without torch, and without the budget in "tried anyway" and `fit: "off"`; `memory_budget_mb`,
  `memory_free_mb()` and `attempt`; peaks in `done` and `error`; `classify` with cause chains and
  the new forms. AC6 tests, and the uncapped half of AC7.
- [ ] 6. **`ctx.fallbacks`.** Speed-only ways, torch's `OutOfMemoryError` only, no exception kept,
  `gc.collect()` and the cache emptied before the next way, peak reset with the failed way's peak
  kept as a lower bound, `node.step_oom`. Tests.
- [ ] 7. **Executors and the runtimes' target.** `NodeError` carries peaks; per-job environment
  (`CUDA_VISIBLE_DEVICES=-1` for `cpu` jobs); the child's pid to a listener; the `died` message
  (-9 on Linux, 0xC0000017 and 0xC000012D on Windows); `Runtimes.device_target()` with the lock
  hash. Tests.
- [ ] 8. **The scheduler.** Cache first, fit, the cache down to the fit, a key per attempt, job
  fields, the events in order, `made_with` and `reduced_input`, learning, one retry (new process
  or in the engine), no retry without a model. AC5 tests, and the scheduler half of AC3 with
  engine test nodes.
- [ ] 9. **The server.** Wire the machine, targets, settings and store into the scheduler;
  `nodes.fit` and `nodes.forget`. AC3 in the tiny runtime, and the rest of AC7.
- [ ] 10. **The bench.** `engine/tests/hardware/test.vram/`, `bench_fit.py` (any node at given
  settings, the context size from nvidia-smi, the Windows per-process counter through `ctypes`
  with its control run, a holder process), `npm run bench:fit`, and a test of its plumbing on the
  processor.
- [ ] 11. **Docs.** `docs/nodes.md` (memory models, `bench:fit`, `ctx.fallbacks`, Windows' Sysmem
  Fallback Policy), `docs/architecture.md` (the Memory part, events, kind `memory`), `AGENTS.md`,
  `docs/handoff.md`.
- [ ] 12. **The owner, on the test card: AC8.** Run the Verification commands in a local session
  and commit the report under `specs/002-fit-to-memory/reports/`.
