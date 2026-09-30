# 002: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [ ] 1. **Machine profiles and system memory.** `hardware.py` reads total and free system memory;
  `engine/tests/fixtures/machines.py` holds the eight profiles. Tests for both.
- [ ] 2. **The memory model in manifests.** Parse and check `memory` in `manifest.py` (with
  `memory.py`), add the `precision` param. AC1 tests.
- [ ] 3. **The estimate, the margin and the fit.** `memory.estimate()`, the margin with its
  `settings.json` override, `memory.fit()`, and `Step.explicit` in `graph.py`. The test table,
  the two extra cases and the renamed-cards run (AC2).
- [ ] 4. **Corrections.** The store in `memory.py`: record, look up, forget, bound, atomic write.
  AC4 tests.
- [ ] 5. **The child.** Cap without torch, `ctx.memory_budget_mb`, the peak on the processor, the
  new out-of-memory forms. AC6 tests, and the no-torch half of AC7.
- [ ] 5b. **`ctx.fallbacks`,** the retry of one step inside the node (plan decision 10), with its
  tests.
- [ ] 6. **The runtimes' target.** `Runtimes.device_target()`: the installed build's vendor and
  its card. Tests.
- [ ] 7. **The scheduler.** Cache candidates, fit, job fields, `node.fit`, `made_with`,
  corrections recorded, one retry with `node.oom`. AC5 tests, and the scheduler half of AC3 with
  an engine test node.
- [ ] 8. **The server.** Wire the machine, targets and corrections into the scheduler;
  `nodes.fit` and `nodes.forget`. AC3 in the tiny runtime, and the rest of AC7.
- [ ] 9. **The bench.** `engine/tests/hardware/test.vram/`, `bench_fit.py`, `npm run bench:fit`,
  and a CPU-only dry run of the bench in a test (no GPU needed for its plumbing).
- [ ] 10. **Docs.** `docs/nodes.md` (writing and measuring a memory model), `docs/architecture.md`
  (the Memory part, events, kind `memory`), `AGENTS.md`, `docs/handoff.md`.
- [ ] 11. **The owner, on the test card: AC8.** Run the Verification commands in a local session
  and commit the report under `specs/002-fit-to-memory/reports/`.
