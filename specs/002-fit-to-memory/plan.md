# 002: Plan

Status: draft, for the owner's approval (decision 10 added on the owner's question, 2026-09-30)

The spec is [spec.md](spec.md); the research behind it is [research.md](research.md).

## Decisions for the owner

Choices the spec leaves open. Each has the answer this plan uses. Approving the plan approves
these answers; say if one should change.

1. **Precision is a param the engine adds.** A node with a memory model gets a `precision` choice
   param whose choices are the precisions its memory model lists, the first being the default.
    - The graph can then set it like any other param.
    - It is part of the cache key, because it affects the output.
    - A fit changes it like any other setting.
    - The node reads it as `ctx.precision`, as today.

   A manifest may not declare its own `precision` param. Nodes without a memory model are
   unchanged, and so are their existing cache keys: no cache key version bump.
2. **How a formula is written.** Working memory is a base plus a sum of terms. Each term is a
   coefficient times a product of factors. A factor is:
    - one of the node's numeric params;
    - an input's `width`, `height` or `pixels` (read from that input's `meta`);
    - `bytes`: 4 at fp32, 2 at fp16 and bf16.

   This is ComfyUI's form (area × bytes × a per-model factor), and it covers the planned models'
   settings: resolution, tokens, chunk size, octree resolution. It is a product of known numbers,
   not a language; nothing is evaluated.
3. **The margin can be overridden in `<data>/settings.json`,** as `{"memory": {"margin_mb": N}}`.
    - That is the first engine setting, so the file is new.
    - No page for it in this spec (workspace UI).
    - The margin in force is reported in every `node.fit`.
4. **Which device is "here".**
    - `cuda` is available to a runtime node when its installed build is an NVIDIA build and the
      machine profile has the card that build was planned for. That card is the one spec 001's
      plan picked, and the child already sees only that card.
    - The engine process never uses a GPU, so engine nodes fit only on `cpu`.
    - `mps`, `xpu` and `rocm` are not read yet and are skipped with that reason.
5. **What counts as the measured peak.**
    - On a card: torch's peak reserved memory, which the child already reports; that is what the
      allocator really held.
    - On the processor: the growth of the child's peak resident memory while the node ran. The
      child reads it with the standard library only: `resource` on Linux, `GetProcessMemoryInfo`
      through `ctypes` on Windows.
    - A run that runs out of memory also records its peak. It is a lower bound, so the retry and
      later fits still learn from it.
6. **Corrections.** One entry per node id, node version, memory-model hash and kind of device
   (`cpu`, or `cuda:<compute capability>`), holding the largest ratio seen and when it was last
   seen.
    - The file is `<data>/memory/corrections.json`, written to a temporary file and renamed into
      place.
    - It holds at most 256 entries; the least recently seen goes first.
    - A ratio below 1 is kept too, so an estimate that is too high also improves. The largest
      ratio wins, so the correction only ever errs on the side of more memory.
7. **"Tried anyway"** (decision 3 of the spec).
    - It happens only when nothing fits on any device *and* a change was skipped because the
      graph sets its param.
    - The node then runs on its first available device, with every change it is allowed, capped
      at the budget, and `node.fit` carries the warning.
    - When nothing fits and nothing the person set was in the way, the node fails before loading
      with kind `memory`.
8. **How a result says how it was made.** The engine writes `made_with` into each output's `meta`:
   the device, the precision, the changes, and `reduced` when a change cost quality. It
   overwrites a node's own `made_with` key, so a node cannot claim a fit it did not get.
9. **The hardware test node** lives in `engine/tests/hardware/test.vram/`, outside `nodes/`, so the
   app never lists it.
    - It allocates exactly what its memory model says, so the report measures the mechanism, not
      a model.
    - It imports torch, which the engine's environment does not have, so that one import carries
      a pyright ignore.
    - A new `npm run bench:fit` runs it and writes the AC8 report.
10. **Retrying one step inside the node: `ctx.fallbacks`.** This is the narrow retry the
    research found in ComfyUI (a failed decode redone in tiles). It costs no reload, so it comes
    before the engine's retry. A node lists ways to do one step, cheapest first:

    ```python
    mesh = ctx.fallbacks("decode", [
        ("whole", lambda: decode(latents), "speed"),
        ("tiles 512", lambda: decode_tiled(latents, 512), "speed"),
        ("tiles 256, fewer faces", lambda: decode_tiled(latents, 256, coarse=True), "quality"),
    ])
    ```

    - On an out-of-memory error, and only that (as `classify` reads it), the helper does three
      things:
        - leaves the `except` block before trying again, so the error's traceback no longer holds
          the failed tensors;
        - empties torch's cache if torch is loaded;
        - emits `node.step_oom` with the step, the way that failed and its peak.

      Then it tries the next way.
    - Any other error goes straight up. When every way runs out of memory, the last error goes
      up as `oom`, and the engine's retry takes over.
    - The way that finished goes into `made_with`. A way marked `quality` sets `reduced`, so a
      result made the cheaper way says so.
    - A node that never calls it is unaffected.

    The order is: the fit before loading, then this retry inside the process, then one engine
    retry with a smaller fit, then failure.

## Files

New:

| File | Why |
| --- | --- |
| `engine/src/oneframe/memory.py` | The memory model: parse and check it, `estimate()`, the margin, `fit()` (pure), and the corrections store |
| `engine/src/oneframe/bench_fit.py` | The AC8 report: profile, context size, three settings, a forced overrun, Windows shared-memory samples, Markdown out |
| `engine/tests/test_memory.py` | AC1, AC2, AC4 and the fit half of AC5 |
| `engine/tests/fixtures/machines.py` | The eight machine profiles of the spec's acceptance criteria, as profile dicts |
| `engine/tests/hardware/test.vram/node.json`, `node.py` | The AC8 node: allocates its weights and working memory as its model says |
| `specs/002-fit-to-memory/reports/` | Where AC8's report is committed |

Changed:

| File | Why |
| --- | --- |
| `engine/src/oneframe/manifest.py` | Read `memory`; add the `precision` param; refuse a bad model with every problem named |
| `engine/src/oneframe/graph.py` | A `Step` remembers which params the graph set (`explicit`), because the fit must not change them |
| `engine/src/oneframe/hardware.py` | System memory, total and free, in the profile (Linux `/proc/meminfo`, Windows `GlobalMemoryStatusEx`, otherwise unknown) |
| `engine/src/oneframe/runtimes.py` | `device_target(runtime_id)`: the installed build's vendor and the card it was planned for |
| `engine/src/oneframe/scheduler.py` | Cache lookup across reductions; fit before running; the job carries the fit and the cap; one retry on `oom`; corrections; `made_with`; the new events |
| `engine/src/oneframe/child.py` | The cap without torch; `ctx.memory_budget_mb`; `ctx.fallbacks`; the peak on the processor; more out-of-memory forms |
| `engine/src/oneframe/server.py` | `nodes.fit` and `nodes.forget`; the scheduler gets the machine, the runtimes' targets and the corrections |
| `engine/tests/test_scheduler.py`, `test_runtime_server.py`, `test_hardware.py`, `test_ports_and_manifests.py` | AC3, AC5, AC6, AC7 and the system-memory parsing |
| `package.json` | `bench:fit` |
| `docs/nodes.md` | How to write a memory model, and how to measure one |
| `docs/architecture.md` | The Memory part, the events, the failure kind `memory` |
| `AGENTS.md` | The hardware-claims rule says "the fit that finished" instead of "the arrangement"; the `bench:fit` command |
| `docs/handoff.md` | State and the spec's status |

## Design

### The memory model in a manifest

```json
"memory": {
  "weights": {
    "fp32": { "mb": 3000, "source": "specs/002-fit-to-memory/reports/<report>.md (GTX 1070)" },
    "fp16": { "mb": 1500, "source": "..." }
  },
  "working": {
    "mb": 200,
    "terms": [
      { "coef": 0.00025, "of": ["resolution", "resolution", "bytes"] },
      { "coef": 0.25, "of": ["chunk_size"] }
    ],
    "source": "..."
  },
  "changes": [
    { "set": { "chunk_size": 2048 }, "costs": "speed" },
    { "set": { "chunk_size": 512 }, "costs": "speed" },
    { "set": { "precision": "fp16" }, "costs": "quality" },
    { "set": { "resolution": 512 }, "costs": "quality" }
  ]
}
```

- A figure nobody has measured is `"mb": null` with a source that says so; the estimate is then
  unknown.
- The checks (AC1):
    - every `set` names a param (or `precision`) with a valid value;
    - a `speed` change sets only params marked `"affects": "speed"`, so its result is the same;
    - a `quality` change sets at least one output-affecting param or `precision`;
    - no `quality` change comes before a `speed` one;
    - every factor names a numeric param, `bytes`, or `<input>.width|height|pixels` of a real
      input;
    - every figure has a source.

### The fit

`fit(manifest, params, explicit, inputs, machine, corrections, margin_mb=None) -> Fit` is a pure
function.

1. **The estimate** for a set of values on a device is:
    - the weights for the precision, plus working memory;
    - times the correction for that node and kind of device, if any;
    - unknown if any figure it needs is null.
2. **The margin** for a device is `margin_mb` from the settings if set. Otherwise it is the
   larger of 1611 MB (1.5 GiB) and 10% of the device's total memory. The budget is the free
   memory less the margin.
3. **The walk.** For each available device in `devices` order:
    - try the values as they stand;
    - then apply the changes cumulatively, in order, skipping any that sets a param in
      `explicit`;
    - the first estimate within the budget wins.
4. **Unknowns.** An unknown estimate, or a device whose free memory cannot be read, ends the walk
   at once: the node runs at its values on that device, and the fit says why.
5. **Nothing fits:** "tried anyway" (decision 7), or kind `memory`. The message names the smallest
   estimate on each device and its free memory.
6. **The result.** `Fit` holds:
    - the device, the values, and the changes applied and skipped (with the reason);
    - the estimate (known, unknown or corrected), the budget, the margin and the free memory;
    - the warning, if any;
    - the index of the last change applied, which the retry starts after.

   The fit never reads a card's name.

### The run

For each step, in the scheduler:

1. **Cache first.** Compute the key for the step's values, then for the values after each
   quality change in order (speed changes do not change the key). Serve the first key the cache
   holds. This reads no machine state, so it is the same on every machine.
2. **Fit.** Read the machine (the profile, refreshed) and the runtime's target, then fit, and emit
   `node.fit`. The job gets `device`, `params` (with the fitted values), `precision`,
   `memory_budget_mb`, and `vram_cap_mb` (the budget, for `cuda` only).
3. **Run.** On success, record the correction (peak ÷ estimate) and write `made_with` into every
   output's `meta` before the cache commit. `node.done` carries the fit.
4. **Retry.** On `oom`:
    - record the correction from the failed attempt, and emit `node.oom` with its peak;
    - refit with the refreshed profile, starting after the last change applied (or on the next
      device when none are left), and run once more in a new process;
    - a second `oom` fails the node with both fits and both peaks.

   Any other kind, and Stop, go through as today.

### The child

- `_cap_allocator` imports torch only if it can. Without torch, it emits
  `ceiling {applied: false, why}` and carries on.
- `NodeContext.memory_budget_mb` comes from the job.
- `NodeContext.fallbacks(step, ways)` is decision 10. What it chose is kept on the context and
  returned in `done`, and the scheduler folds it into `made_with`.
- `classify` adds `CUBLAS_STATUS_ALLOC_FAILED`, `CUDA error: out of memory` and `MemoryError`.
- `done` and `error` gain `peak_ram_mb` when the device is `cpu`.

### Events and methods

- `node.fit`: `step`, `node`, `device`, `free_mb`, `margin_mb`, `budget_mb`, `estimate_mb`,
  `estimate` (known, unknown or corrected), `changes`, `skipped`, `warning`.
- `node.oom`: `step`, `attempt`, `peak_mb`, `message`.
- `node.step_oom`: `step`, `stage`, `way`, `next`, `peak_mb` (from `ctx.fallbacks`).
- `nodes.fit {node, params?, inputs?}` returns the same shape as `node.fit`. `inputs` maps a port
  to `{width, height}`, because an input's size is not known before a run.
- `nodes.forget {node}` returns the number of corrections dropped.
- Neither is added to the page's allowlist.

## The test table (AC2)

The test node is the manifest above:

| Defaults | resolution 1024, chunk 8192, precision fp32 |
| --- | --- |
| Estimate at the defaults | 6297 MB |
| After change 1 | 4761 MB |
| After change 2 | 4377 MB |
| After change 3 | 2352 MB |
| After change 4 | 1959 MB |

MB are 10^6 bytes, as the profile reports them.

| Machine | Free / total (MB) | Margin | Budget | Expected fit |
| --- | --- | --- | --- | --- |
| No GPU, 32 GB system memory | system 24000 / 34360 | 3436 | 20564 | `cpu`, defaults |
| 4 GB card, compute 5.2 (16 GB system) | 3900 / 4295 | 1611 | 2289 | `cuda`, changes 1–4, `reduced` |
| 8 GB card, compute 6.1, 1.5 GB held by other programs | 7000 / 8590 | 1611 | 5389 | `cuda`, change 1 only |
| 12 GB card, compute 8.6 | 12000 / 12885 | 1611 | 10389 | `cuda`, defaults |
| 24 GB card, compute 8.9 | 24500 / 25770 | 2577 | 21923 | `cuda`, defaults |
| 80 GB card, compute 9.0 | 84000 / 85899 | 8590 | 75410 | `cuda`, defaults |
| Two cards, 8 GB and 24 GB | as the rows above | | | the 24 GB card (the runtime's plan), defaults |
| 2 GB card, compute 5.2, 4 GB system | card 1900 / 2147; system 3000 / 4295 | 1611 | 289; 1389 | fails before loading: kind `memory`, "needs 1959 MB at the smallest settings; 1900 MB free on the card, 3000 MB free in system memory" |

Also:
- On the 2 GB machine with the graph setting resolution 1024, the node is tried anyway on `cuda`
  with changes 1–3 and a warning.
- On the 8 GB machine with the graph setting chunk 8192, changes 1 and 2 are skipped, naming
  `chunk_size`, and change 3 fits.
- Every row is run again with every card renamed, and must give the same fit.

## Risks

| Risk | What catches it |
| --- | --- |
| The estimate is wrong for a real model on a card nobody here has | The margin, the cap turning an overrun into `oom`, the one retry, and the correction on that machine. Each real node's model is measured at several settings (spec 005) |
| Free memory changes between the measurement and the load | The margin covers other programs' growth; the cap keeps our own overrun an `oom` |
| The cap does not cover the CUDA context or extensions' own allocations (research) | The margin covers the context, and the AC8 report measures it. Windows' Sysmem Fallback Policy stays the person's setting (out of scope) |
| Torch's cap is a fraction of total memory, not free | The fraction is computed from the budget, so the cap equals the budget |
| A node changes its `made_with` or lies about its peak | `made_with` is written by the engine after the node returns; the peak comes from the child's own measurement |
| Reading `/proc/meminfo` or `GlobalMemoryStatusEx` fails | System memory is then unknown, and a CPU fit runs at its values and says so |
| The test-table numbers drift from the formula | The table is computed in the test from the manifest, and asserted against the values above |

## Tests

- **Added:**
    - `test_memory.py`:
        - the manifest checks, each refusal with every problem (AC1);
        - the estimate and the formula factors;
        - the margin and its override;
        - every row of the test table, the two extra cases, and the renamed-cards run (AC2);
        - corrections: scale, device kind, version bump, forget, the bound, an interrupted write
          (AC4);
        - the cache candidates in order (AC5).
    - `test_scheduler.py`:
        - a cached unreduced result is served on a machine that would reduce;
        - `made_with` in `meta` and in `node.done`;
        - `fp32` and `fp16` keys differ, and a device change alone does not change the key (AC5);
        - out-of-memory forms (AC6);
        - `ctx.fallbacks`, run by an engine test node:
            - a first way that runs out of memory is followed by the second, in the same
              process, with `node.step_oom`;
            - the finishing way is in `made_with`, and a `quality` way sets `reduced`;
            - another error kind is not retried;
            - when every way runs out, the engine's retry follows;
            - the failed way's objects are released before the next way starts (a weak
              reference to them is dead by then).
    - `test_runtime_server.py`:
        - in the tiny runtime's `cu130` build, on a recorded compute 7.5 profile:
            - `oom` → one retry in a new process → done, with the events in order;
            - a second `oom` fails with both fits and nothing cached;
            - another kind, and Stop, give no retry (AC3);
            - the `ceiling` event says the cap was not applied (AC7);
        - `nodes.fit` and `nodes.forget` with sockets blocked and no heavy imports (AC7).
    - `test_hardware.py`: system memory from recorded `/proc/meminfo` text, and from a stubbed
      Windows call.
- **Removed:** none.

## Verification

| Criterion | How |
| --- | --- |
| AC1, AC2, AC4, AC5, AC6 | Unit tests above, in `npm run engine:check`, in CI on Windows and Linux |
| AC3, AC7 | Integration tests above, with the real uv and the tiny runtime, in CI on Windows and Linux |
| AC8 (hardware) | The owner's PC, below |

AC8, on the owner's Windows PC, from a checkout of the PR's branch, in a local session
(`/local-session`):

```
npm run engine:sync
npm run bench:fit -- --out specs/002-fit-to-memory/reports/
```

`bench:fit` installs the torch runtime if needed (as `bench:runtime` does), then:

1. **Context size.** It measures the CUDA context: nvidia-smi's used memory for the process after
   `torch.cuda.init()`, less torch's reserved memory.
2. **Three settings.** It runs `test.vram` at three settings and records, for each:
    - the estimate, the measured peak, and the ratio;
    - seconds;
    - free memory before the run.

   The criterion holds when every ratio is within 10% or 64 MB of 1.
3. **A tight budget.** It reruns with `settings.json` setting a margin that leaves only the
   smallest setting's budget, and records the fit it chose and that the run finished.
4. **A forced overrun.** It runs with a param that makes the node allocate 30% more than its
   model says, and records:
    - the cap, and the `oom` from the cap;
    - Windows' `\GPU Adapter Memory(*)\Shared Usage` sampled every 250 ms with `Get-Counter`
      before, during and after the attempt;
    - the retry's fit, and that it finished.

   It runs twice. First, `test.vram`'s working step uses `ctx.fallbacks` (whole, then in two
   halves): the report shows `node.step_oom` and the second way finishing in the same process,
   with no reload. Second, with a param that disables the fallbacks: the report shows the
   engine's retry in a new process. It records the seconds of each, so the saving of the narrow
   retry is measured.

   The criterion holds when shared usage does not rise during either overrun.
5. **The report.** It writes `YYYY-MM-DD-<card>-fit.md` with every number, and says plainly what
   was not measured. The report names the card because it is evidence; nothing in the code reads
   that name.

The owner commits the report; the PR marks AC8 passed only then.
