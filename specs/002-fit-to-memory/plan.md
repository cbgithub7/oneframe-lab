# 002: Plan

Status: draft, for the owner's approval (revised 2026-09-30 for the amended spec, and 2026-10-01 after the third review)

The spec is [spec.md](spec.md); the research behind it is [research.md](research.md).

## Decisions for the owner

Choices the spec leaves open. Each has the answer this plan uses. Approving the plan approves
these answers; say if one should change.

1. **Precision is a param the engine adds.** A node with a memory model gets a `precision` choice
   param whose choices are the precisions its model lists, the first being the default.
    - The graph sets it like any other param.
    - It is part of the cache key.
    - The fit changes it like any other setting.
    - The node reads it as `ctx.precision`.
    - `nodes.list` shows it, so the page sees it: `Manifest.to_json` adds the params the engine
      made.

   A manifest may not declare its own `precision`. Nodes without a memory model, and their cache
   keys, are unchanged.
2. **How the memory model is written.**
    - **Weights** are a table keyed by precision, and optionally also by one choice param named
      in `weights_by` (the checkpoint).
    - **Each precision** is one of `fp32`, `fp16` or `bf16`, and states `cuda_min_capability`
      (or none), `cpu` (true or false), and optionally `bytes`, the bytes per value its working
      memory uses (4 at fp32 and 2 at fp16/bf16 by default; a mixed-precision node states its
      own). On a card whose capability is unknown, a precision that needs one is not used.
    - **Working memory** is a base plus terms, each a coefficient times a product of factors. A
      factor is:
        - one of the node's numeric params;
        - `bytes`;
        - `<port>.<field>`, a numeric field of that input's `meta` (`image.width`,
          `views.count`); `<port>.pixels` is width × height;
        - a bool param, as 0 or 1;
        - a choice param, through a table in the model giving a number per choice
          (`"factors": {"pipeline_type": {"512": 1, "1024": 4}}`);
        - `weights`: the weights' size for the current precision and checkpoint.

      Coefficients may be negative; a formula's result is never below 0.
    - **Missing inputs:** an unconnected optional input counts as 0. A connected input without
      the field makes the estimate unknown.
    - **Other figures:**
        - `weights_on_device` is a formula of the same form, defaulting to `weights`. It is how
          offloading lowers the device's need: for example `weights − 0.9 × weights × offload`.
        - `system_mb` is a formula of the same form, defaulting to `weights`.
        - `outside_torch_mb` is a number, default 0.
    - **Time** is optional, per kind of device: `{"seconds", "at", "source"}`, where `at` names
      the settings it was measured at.

   The formula is a product of known numbers, not a language; nothing is evaluated.
3. **Settings live in `<data>/settings.json`,** the engine's first settings file. The margin is
   set per device type (`cuda`, `cpu`); a kind of device, which the learned store uses, is finer
   (`cuda:6.1`).

   ```json
   { "memory": { "margin_mb": { "cuda": null, "cpu": null }, "never_reduce_quality": false, "fit": "on" } }
   ```

   `null` means the default margin. The file is read at each fit. The page's editor for it is
   the workspace UI's job.
4. **Which devices are here.**
    - `cuda` is available to a runtime node when its installed build is an NVIDIA build and the
      profile has the card that build was planned for (spec 001's choice; the child sees only
      that card).
    - Engine nodes fit only on `cpu`, because the engine process never uses a GPU.
    - `mps`, `xpu` and `rocm` are skipped as not read yet.
    - A `cpu` job in a GPU build gets `CUDA_VISIBLE_DEVICES=-1` in its own environment, so the
      executor's environment becomes per job. (`-1` rather than an empty value, which some
      platforms read as unset; the AC7 test checks a child sees no card.)
    - The scheduler reads nvidia-smi only for a runtime node that lists `cuda`. Its defaults
      describe a machine with no card and unknown memory, so tests never depend on the machine
      they run on; the server passes the real reader.
5. **What is measured.**
    - On a card: torch's peak reserved memory. For each `ctx.fallbacks` way the peak is reset;
      the way that ran out records its peak as a lower bound, so a node that always needs a
      fallback does not learn a ratio below 1.
    - On the processor: the growth of the child's peak resident memory, read with the standard
      library (`resource` on Linux, `GetProcessMemoryInfo` through `ctypes` on Windows).
    - Engine nodes are not measured, because the engine process's peak covers its whole life.
    - The child reports its peaks (`peak_reserved_mb`, `peak_ram_mb`) in `done` and in `error`.
      `NodeError` carries them, so the scheduler can report the peaks of both attempts and learn
      from failures.
6. **What each machine learns: `<data>/memory/learned.json`,** with a format version, written to a
   temporary file and renamed into place.
    - **Key:** one entry per node id, node version, memory-model hash, runtime lock hash and kind
      of device (`cpu`, `cuda:<capability>`, or `cuda:unknown`).
    - **Contents:**
        - the last 8 working-memory ratios, each measured working memory (peak − weights on the
          device − outside torch) over the *uncorrected* working estimate. A run whose working
          estimate is under 64 MB adds no ratio, because the ratio would be noise;
        - the measured peak per settings hash, for the last 16 settings;
        - the seconds per settings hash, for the last 16 settings.

      A settings hash covers the values, the precision and every input factor (an input's size),
      so a peak measured at 512 × 512 is never used for 4096 × 4096.
    - **The correction** is the largest of the recent ratios. It may go below 1 only when at
      least 2 successful runs agree. A failed run adds its ratio only when it is above 1, as a
      lower bound.
    - **The bound:** 256 entries; the least recently used goes first.
7. **"Tried anyway"** happens only when nothing fits anywhere *and* a change was skipped because
   the graph sets its param. The node then runs on its first device where its precision runs,
   with every change it is allowed, and `node.fit` carries the warning. Otherwise nothing fitting
   is a `memory` failure.
8. **`made_with`** is written by the engine into each output's `meta`. It holds:
    - the device and precision, and each changed setting;
    - the `ctx.fallbacks` way each step finished with;
    - `reduced` when a change cost quality;
    - `reduced_input` when any input was reduced or made from a reduced input.

   It overwrites a node's own `made_with`, so a node cannot claim a fit it did not get.
9. **`npm run bench:fit -- <node> [--set k=v ...]...` measures any node** at the settings given,
   one run per `--set` group, and writes a report of estimates, peaks and seconds. Spec 005 uses
   it to measure real models; fitting coefficients from the numbers is left to spec 005. With no
   node it runs the AC8 test node, `engine/tests/hardware/test.vram/`, which lives outside
   `nodes/` so the app never lists it. That node imports torch, which the engine's environment
   lacks, so its one import carries a pyright ignore.
10. **`ctx.fallbacks(step, ways)`** retries one step a cheaper way in the same process. Each way is
    `(name, fn)`, and every way must give the same result within rounding.
    - It retries only on torch's `OutOfMemoryError`, found by the type's name, anywhere in the
      error's cause chain. Other CUDA memory errors go to the engine's retry, because the context
      may not be usable after them.
    - Before the next way it leaves the `except` block and keeps no exception object, so the
      traceback's frames are gone. It then runs `gc.collect()`, because traceback cycles can keep
      tensors alive, and empties torch's cache.
    - It resets the peak statistics and emits `node.step_oom`.
    - When every way runs out of memory, the last error goes up as `oom`.
11. **The cap is set inside the child,** after `torch.cuda.init()`:
    - `min(budget, mem_get_info free − 256 MB) − outside_torch_mb`;
    - in "tried anyway" and with `fit: "off"`, the budget is left out:
      `mem_get_info free − 256 MB − outside_torch_mb`. Capping at a budget the fit already knows
      is too small would make those runs fail for certain;
    - 256 MB is the headroom ComfyUI's allocator, comfy-aimdo, keeps by default
      ([research.md](research.md));
    - `ceiling` reports the budget, the free memory it saw, and the cap.
    - `ctx.memory_free_mb()` is the cap less torch's active memory (ComfyUI's free-memory
      formula, bounded by our cap). On the processor it is the system budget less the growth of
      the child's resident memory.
12. **Events:**
    - `node.fit` comes before each attempt's `node.start`, and `node.start` carries `attempt`.
    - An engine node's retry happens in the engine's process.
    - A runtime node with no memory model gets a `node.fit` (estimate unknown, a budget) and is
      capped, but never retried.
    - An engine node with no memory model gets no `node.fit` and no cap, as today. The existing
      tests' exact event lists (`test_scheduler.py`, which use engine nodes) therefore stay as
      they are.
13. **Runtime code stays readable by older Pythons.** The repo formats for Python 3.14, and ruff
    then rewrites `except (A, B):` as `except A, B:`, which is a syntax error before 3.14.
    `child.py` runs inside every runtime's own Python (the tiny runtime's is 3.11). So:
    - `ruff.toml` gains `per-file-target-version` of `py311` for `engine/src/oneframe/child.py`,
      `engine/tests/runtimes/**` and `engine/tests/hardware/**`;
    - `pyrightconfig.json` gives those paths `pythonVersion` 3.11, so newer syntax fails the
      check;
    - the AC3 and AC7 tests already run `child.py` under 3.11, which proves it.
14. **The context knows its attempt.** `ctx.attempt` is 1, or 2 on the engine's retry. The AC8
    node's overrun param applies only to attempt 1, so the engine's retry can get past it.

## Files

New:

| File | Why |
| --- | --- |
| `engine/src/oneframe/memory.py` | The memory model (parse, check, hash), `estimate()`, margins and settings, `fit()` (pure), and the learned store |
| `engine/src/oneframe/bench_fit.py` | `bench:fit`: any node at the settings given; the AC8 run; the Windows per-process performance counter through `ctypes`; Markdown out |
| `engine/tests/test_memory.py` | AC1, AC2, AC4, and the fit half of AC5 |
| `engine/tests/fixtures/machines.py` | The profiles of the spec's acceptance criteria, as profile dicts |
| `engine/tests/hardware/test.vram/node.json`, `node.py` | The AC8 node: allocates its weights and working memory as its model says, with a `ctx.fallbacks` step, and a param that overruns |
| `specs/002-fit-to-memory/reports/` | Where AC8's report is committed |
| `specs/002-fit-to-memory/research.md` | Adds comfy-aimdo's 256 MB default headroom, the source of decision 11's reserve |

Changed:

| File | Why |
| --- | --- |
| `engine/src/oneframe/manifest.py` | Read `memory`; add `precision`; refuse a bad model with every problem named; `to_json` shows the added param |
| `engine/src/oneframe/graph.py` | A `Step` remembers which params the graph set (`explicit`) |
| `engine/src/oneframe/hardware.py` | System memory, total and available: Linux `MemAvailable`; Windows `GlobalMemoryStatusEx`, taking the smaller of available physical memory and available commit, because Windows refuses allocations past its commit limit even with memory free; otherwise unknown. The reader is a parameter, so tests that pass `platform="win32"` on Linux never reach `ctypes.windll` |
| `engine/src/oneframe/runtimes.py` | `device_target(runtime_id)`: the installed build's vendor, its card, and the lock hash |
| `engine/src/oneframe/executors.py` | `NodeError` carries the peaks; a per-job environment (for `CUDA_VISIBLE_DEVICES=-1`); the child's pid reported to a listener (for AC8's per-process counter); the `died` message |
| `engine/src/oneframe/scheduler.py` | Cache first, fit, key per attempt, job fields, events, `made_with`, learning, one retry |
| `engine/src/oneframe/child.py` | The cap in the child, with or without torch; `memory_budget_mb`, `memory_free_mb()`, `fallbacks`; peaks in `done` and `error`; the out-of-memory forms and cause chain |
| `engine/src/oneframe/server.py` | `nodes.fit`, `nodes.forget`; the scheduler gets the machine, targets, settings and store |
| `ruff.toml`, `pyrightconfig.json` | Decision 13: code that runs in runtimes stays valid on Python 3.11 |
| `engine/tests/test_scheduler.py`, `test_runtime_server.py`, `test_hardware.py`, `test_ports_and_manifests.py` | AC3, AC5, AC6, AC7, system memory |
| `package.json` | `bench:fit` |
| `docs/nodes.md` | Writing a memory model; measuring it with `bench:fit`; `ctx.fallbacks`; Windows' Sysmem Fallback Policy for people who run the app |
| `docs/architecture.md` | The Memory part, the events, the failure kind `memory` |
| `AGENTS.md` | "the fit that finished" in the hardware-claims rule; the `bench:fit` command |
| `docs/handoff.md` | State and the spec's status |

## Design

### The memory model in a manifest

This is the test node's model; the numbers are what the test table uses.

```json
"memory": {
  "precisions": {
    "fp32": { "cuda_min_capability": null, "cpu": true },
    "fp16": { "cuda_min_capability": "6.0", "cpu": false },
    "bf16": { "cuda_min_capability": "8.0", "cpu": true }
  },
  "weights": { "fp32": 3000, "fp16": 1500, "bf16": 1500, "source": "test node, exact by construction" },
  "working": {
    "mb": 200,
    "terms": [
      { "coef": 0.00025, "of": ["resolution", "resolution", "bytes"] },
      { "coef": 0.25, "of": ["chunk_size"] }
    ],
    "source": "test node, exact by construction"
  },
  "changes": [
    { "set": { "chunk_size": 2048 }, "costs": "speed" },
    { "set": { "chunk_size": 512 }, "costs": "speed" },
    { "set": { "precision": "fp16" }, "costs": "quality" },
    { "set": { "resolution": 512 }, "costs": "quality" }
  ],
  "upgrades": [ { "set": { "chunk_size": 32768 } } ]
}
```

- With `weights_by: "checkpoint"`, `weights` becomes `{ "<choice>": { "<precision>": mb } }`.
- The checks (AC1):
    - every `set` names a param (or `precision`) with a valid value;
    - a `speed` change and every upgrade set only params marked `"affects": "speed"`;
    - a `quality` change sets an output-affecting param, `precision`, or the `weights_by` param;
    - no `quality` change comes before a `speed` one;
    - every precision has its rule for where it runs;
    - `weights_by` names a choice param, and every choice has weights;
    - every factor names a numeric param, `bytes`, or `<port>.<field>` of a real input;
    - every figure has a source (`null` values say why).

### The fit

`fit(model, values, explicit, inputs, machine, target, learned, settings) -> Fit` is a pure
function.

1. **Estimate.** For values on a device, all in unrounded MB, fitting when need ≤ budget:
    - on a card: device need = weights on the device + working × correction + outside torch,
      against the card's budget; and system need = the `system_mb` formula, against the system
      budget;
    - on the processor, the device is system memory: the need is the larger of (weights +
      working × correction) and `system_mb`, against the system budget. The two are not added,
      because they are the same memory.

   It is unknown if a figure it needs is null, unless the learned store has a peak for these
   settings on this kind of device.
2. **Margin.** From the settings, or the larger of 1611 MB (1.5 GiB) and 10% of the device's
   total, for cards and for system memory alike.
3. **Devices.** The devices of `devices` that are here (decision 4) and on which the current
   precision can run, in the manifest's order. "The first device" below is the first of these.
   "The node's first listed device" is `devices[0]`, whether or not it is here.
4. **Order.**
    1. With `fit: "off"`: the first device, at the values, and nothing else.
    2. Upgrades, on the first device, if both needs fit. An upgrade that sets a param the graph
       sets is skipped.
    3. Speed only: for each device, the values, then the speed changes cumulatively. A change is
       skipped when it sets an explicit param, or a precision that cannot run on that device.
    4. Unless `never_reduce_quality`: the same with every change, speed and quality, in order.
    5. Nothing fits: "tried anyway" (decision 7), or kind `memory`.
5. **Slower device.** When the fit's device is not the node's first listed device, `Fit.warning`
   is `slow` (also when the machine has no card at all), with:
    - the expected seconds and their basis: `measured` from the learned store, `published` from
      the manifest's `time`, or `unknown`;
    - `alternative`: step 4.4's result on the first device, when there is one and
      `never_reduce_quality` is off.
6. **Unknowns.** An unknown estimate or an unreadable budget ends the walk: the node runs at the
   values on that device, and the fit says why.
7. **The result.** `Fit` holds the device, the values, the changes applied and skipped with
   reasons, the upgrades, the needs, budgets, margins and free memory, the estimate's basis, the
   warning, and the index of the last change applied.

The fit never reads a card's name. A test renames every card and checks every fit is unchanged.

### The run

For each step, in the scheduler:

1. **The unreduced key.** If it is cached, serve it (`node.cached`).
2. **Fit.** Read the machine (a fresh profile) and the runtime's target, then fit, and emit
   `node.fit`. A `memory` failure ends here.
3. **The cache, down to the fit.** Look up the keys after each quality change, in order, up to
   the fitted values, skipping changes that touch `explicit`. Serve the first hit.
4. **Run.**
    - Compute the key for the fitted values and begin its work folder.
    - Emit `node.start` with `attempt`.
    - The job carries `device`, `params` (with the fitted values), `precision`,
      `memory_budget_mb`, `vram_cap_mb` (the budget, for `cuda`), `outside_torch_mb`, and the
      per-job environment.
5. **Success.**
    - Record the peak, ratio and seconds in the learned store (runtime nodes only).
    - Write `made_with` into every output's `meta`, then commit under the fitted key.
    - `node.done` carries the fit.
6. **`oom`.**
    - Record the failed attempt, and emit `node.oom` with its peaks.
    - With nothing left to change, the node fails here with `oom`, and no second process starts.
      That is the case for a node without a memory model, "tried anyway", `fit: "off"`, and an
      attempt that already used the last change on its last device.
    - Otherwise, refit with a fresh profile. The retry must be strictly smaller than the attempt
      that failed:
        - if that attempt used upgrades, the retry drops them and walks from the values;
        - otherwise it applies at least the next change after the last one applied (the next
          change, if the estimate was unknown), then continues the walk until it fits;
        - when no change is left on that device, it continues on the next device, as the walk
          does.
    - Repeat steps 3 to 5 once, in a new process for a runtime node and in the engine's process
      for an engine node.
    - A second `oom` fails the node with both fits and the peaks of both attempts.
7. **Everything else,** including Stop, goes through as today. `node.failed` carries the fit.

### The child

- **The cap:** `_cap_allocator` becomes decision 11. Without torch it emits
  `ceiling {applied: false, why}` and carries on.
- **The context:** `memory_budget_mb`, `memory_free_mb()` and `fallbacks` (decision 10).
- **Peaks:** `done` and `error` carry `peak_reserved_mb`, `peak_vram_mb` and `peak_ram_mb`.
- **`classify`:**
    - walks `__cause__` and `__context__`;
    - adds `CUBLAS_STATUS_ALLOC_FAILED`, `CUDNN_STATUS_ALLOC_FAILED`, `CUDA error: out of memory`
      and `MemoryError`.
- **`died`:** the executor's message for `died` says "may have run out of system memory" when the
  exit code says so: -9 on Linux (the kernel's out-of-memory killer), and on Windows
  0xC0000017 (no memory) or 0xC000012D (commit limit), which Python reports unsigned.

### Events and methods

- `node.fit`: `step`, `node`, `attempt`, `device`, `precision`, `needs`, `free`, `margins`,
  `budgets`, `estimate` (known, unknown, corrected or measured), `changes`, `upgrades`,
  `skipped`, `settings`, `warning` (`slow` with `seconds`, `basis` and `alternative`,
  `tried_anyway`, or `unknown`).
- `node.start`: adds `attempt`.
- `node.oom`: `step`, `attempt`, `peak_reserved_mb`, `peak_ram_mb`, `message`.
- `node.step_oom`: `step`, `stage`, `way`, `next`, `peak_reserved_mb`.
- `nodes.fit {node, params?, inputs?}`: the same shape as `node.fit`. `inputs` maps a port to its
  `meta` fields.
- `nodes.forget {node}`: the number of entries dropped.
- Neither method is added to the page's allowlist.

## The test table (AC2)

The test node is the model above. Its defaults are resolution 1024, chunk 8192, fp32.

| Values | Device need |
| --- | --- |
| Defaults | 6297 MB |
| With the upgrade (chunk 32768) | 12441 MB |
| After change 1 (chunk 2048) | 4761 MB |
| After change 2 (chunk 512) | 4377 MB |
| After change 3 (fp16) | 2352 MB |
| After change 4 (resolution 512) | 1959 MB |

The system need is the weights: 3000 MB at fp32, 1500 MB at fp16. MB are 10^6 bytes, as the
profile reports them.

| Machine | Card free / total | System free / total | Budgets (card; system) | Expected fit |
| --- | --- | --- | --- | --- |
| No GPU, 32 GB | none | 24000 / 34360 | –; 20564 | `cpu` with the upgrade; warning `slow` (no card) |
| 4 GB, compute 5.2, 16 GB | 3900 / 4295 | 10000 / 17180 | 2289; 8282 | `cpu`, defaults; warning `slow`, no alternative (fp16 needs compute 6.0, and fp32 does not fit reduced) |
| 4 GB, compute 7.5, 16 GB | 3900 / 4295 | 10000 / 17180 | 2289; 8282 | `cpu`, defaults; warning `slow`, alternative `cuda` with changes 1–4 |
| 4 GB, compute 7.5, 8 GB | 3900 / 4295 | 5000 / 8590 | 2289; 3389 | `cuda`, changes 1–4, `reduced` (the processor cannot fit either) |
| 6 GB, compute 7.5 | 6000 / 6442 | 10000 / 17180 | 4389; 8282 | `cuda`, changes 1–2 |
| 8 GB, compute 6.1, 1.5 GB held | 7000 / 8590 | 10000 / 17180 | 5389; 8282 | `cuda`, change 1 |
| 12 GB, compute 8.6 | 12000 / 12885 | 24000 / 34360 | 10389; 20564 | `cuda`, defaults (the upgrade does not fit) |
| 24 GB, compute 8.9 | 24500 / 25770 | 50000 / 68719 | 21923; 43128 | `cuda` with the upgrade |
| 80 GB, compute 9.0 | 84000 / 85899 | 200000 / 274878 | 75410; 172512 | `cuda` with the upgrade |
| Two cards, 8 GB and 24 GB | as the rows above | 50000 / 68719 | | the 24 GB card (the runtime's plan), with the upgrade |
| 2 GB, compute 6.1, 4 GB | 1900 / 2147 | 3000 / 4295 | 289; 1389 | kind `memory`: "Needs 3570 MB free on the card (1959 MB at the smallest settings plus a 1611 MB margin); 1900 MB are free. Needs 5201 MB free in system memory for the processor (3590 MB, fp16 cannot run there, plus 1611 MB); 3000 MB are free." |

The extra cases:

| Case | Expected fit |
| --- | --- |
| 2 GB, the graph sets resolution 1024 | tried anyway on `cuda` with changes 1–3 and a warning |
| 8 GB, the graph sets chunk 8192 | changes 1–2 skipped naming `chunk_size`; `cpu` at defaults, warning `slow`, alternative `cuda` with change 3 |
| 8 GB, the graph sets precision bf16 | `cuda` is out (bf16 needs compute 8.0); `cpu` at bf16, warning `slow`, no alternative |
| 12 GB, compute 8.6, with only 4000 / 4295 MB of system memory (system budget 2389) | the card's fit fails on system memory at fp32 (3000 MB of weights); `cuda` with changes 1–3, `reduced` |
| 24 GB, the graph sets chunk 8192 | the upgrade is skipped; `cuda` at defaults |
| Retry after `oom`: 24 GB with the upgrade | the retry drops the upgrade and runs at defaults |
| Retry after `oom`: 8 GB at change 1, fresh budget unchanged | the retry applies change 2 |
| Retry after `oom`: 8 GB, `fit: "off"` | no retry; fails with `oom` |
| A runtime node without a memory model, 8 GB | `cuda` at its defaults, estimate unknown, capped; no retry after `oom` |
| 4 GB 7.5 with 8 GB, `never_reduce_quality` | kind `memory` (only a quality cut would fit) |
| 4 GB 7.5 with 16 GB, `never_reduce_quality` | `cpu`, warning `slow`, no alternative offered |
| 8 GB, `fit: "off"` | `cuda` at defaults, capped at the budget |
| 8 GB, learned correction 1.5 on `cuda:6.1` | change 2 instead of change 1 (the correction scales working memory only) |
| Every row with every card renamed | the same fit |

The table is computed in the test from the manifest and asserted against these values. They
were computed by a script following these rules before being written here, and recomputed
independently by the third review.

## Risks

| Risk | What catches it |
| --- | --- |
| An estimate is wrong for a real model on a card nobody here has | The margin, the cap turning an overrun into `oom`, `ctx.fallbacks`, the one retry, and the correction on that machine. Real nodes are measured at several settings with `bench:fit` (spec 005) |
| Free memory changes between the measurement and the load | The child measures again after its context exists and caps at the smaller figure |
| Extensions allocate outside torch's cap | `outside_torch_mb` lowers the cap; the margin covers the rest; `bench:fit` measures the whole card, not only torch |
| A correction feeds on itself | Ratios are always against the uncorrected estimate, and only the working part is scaled |
| One unlucky run inflates a correction for ever | Only the last 8 runs count; `nodes.forget` clears it |
| A retry inside the process leaves the context broken | `ctx.fallbacks` retries only torch's own `OutOfMemoryError`; everything else goes to a new process |
| The slow warning's time is wrong | Its basis is always stated; "unknown" is said plainly |
| Reading system memory fails | System memory is unknown; a processor fit then runs at its values and says so, and a card's fit skips the system check and says so |
| The test table drifts from the rules | It is computed in the test from the manifest |
| Formatting for 3.14 breaks code that runs in an older runtime | Decision 13: ruff and pyright target 3.11 for that code, and CI runs it under 3.11 |
| The AC8 counter reads nothing and "no growth" passes by mistake | A control run must show the counter rising first (Verification 4) |

## Tests

- **Added:**
    - `test_memory.py`:
        - each manifest refusal, with every problem named (AC1);
        - the estimate and its factors: checkpoint weights, `bytes`, meta fields, missing
          optional inputs;
        - margins and settings;
        - every row and extra case of the test table, and the renamed run (AC2);
        - the learned store: corrections after two runs, no lowering from one run or a failure,
          device kinds, measured peaks for unknown estimates, seconds, the key's parts, forget,
          the bound, an interrupted write (AC4);
        - the cache candidates (AC5).
    - `test_scheduler.py`, with engine test nodes:
        - the cache rules (AC5): served unreduced; a reduced result not served where better is
          possible or where it changes an explicit param; stored under the key it ran with;
          `made_with` and `reduced_input`;
        - `classify` forms and cause chains (AC6);
        - `ctx.fallbacks`: second way in the same process, `node.step_oom`, only on torch's
          out-of-memory error, the failed way's objects released (a weak reference is dead),
          the peak reset;
        - the in-process retry of an engine node;
        - no retry without a memory model, in "tried anyway" or with `fit: "off"`;
        - a node without a memory model runs on the first device this machine has, within a
          budget (AC1);
        - the existing exact event lists for engine nodes without a model are unchanged.
    - `test_runtime_server.py`, in the tiny runtime's `cu130` build on a recorded compute 7.5
      profile:
        - the retry in a new process with events in order, and both peaks on a second failure
          with nothing cached;
        - no retry for another kind or on Stop (AC3);
        - the uncapped `ceiling` event;
        - a `cpu` job that sees no card;
        - `nodes.fit` and `nodes.forget` with sockets blocked and no heavy imports (AC7).
    - `test_hardware.py`: system memory from recorded `/proc/meminfo` text and a stubbed Windows
      reader (including commit smaller than physical memory); `profile(platform="win32")` on
      Linux still works.
    - A test that runs `bench_fit` on the processor, with an engine test node, to prove its
      plumbing without a GPU.
- **Removed:** none.

## Verification

| Criterion | How |
| --- | --- |
| AC1–AC6 | The tests above, in `npm run engine:check`, in CI on Windows and Linux |
| AC7 | Integration tests with the real uv and the tiny runtime, in CI on Windows and Linux |
| AC8 (hardware) | The owner's PC, below |

AC8, on the owner's Windows PC, from a checkout of the PR's branch, in a local session
(`/local-session`):

```
npm run engine:sync
npm run bench:fit -- --out specs/002-fit-to-memory/reports/
```

With no node named, `bench:fit` runs the AC8 test node. It installs the torch runtime if needed
(as `bench:runtime` does), then:

1. **Context size.** In a probe, it reads the whole card's used memory with nvidia-smi (already
   parsed in `hardware.py`) before CUDA starts, and again after a first allocation and kernel.
   With lazy loading, starting CUDA alone creates little, so the first kernel is what loads the
   context. The size is that difference less torch's reserved memory. Per-process figures are not
   available under Windows' display driver, so it does not use them.
2. **Three settings.** It runs the node at three settings and records, for each:
    - the estimate, the measured peak and the ratio;
    - the seconds;
    - the free memory before the run.

   It holds when every ratio is within 10% or 64 MB of 1.
3. **Another program holding memory.** It starts a holder process in the torch runtime that
   allocates enough to leave only the smallest setting's budget, then runs the node. It records
   the fit it chose, and that the run finished.
4. **An overrun past the cap.** It sets the margin so the budget equals the estimate, and runs
   with the param that allocates 30% more on attempt 1 only, so the allocation must exceed the
   cap. It samples the run's `\GPU Process Memory(pid_*)\Shared Usage` counter every 250 ms
   through the Windows performance-counter API (`ctypes`), before, during and after; the
   executor reports the child's pid.
    - **Control first:** with `fit: "off"` and the cap removed, it allocates 256 MB past the
      card's free memory for two seconds. The counter must rise. This is a brief, deliberate
      spill, so a reading of "no growth" later cannot be a counter that reads nothing.

   It then runs the overrun twice:
    1. With `ctx.fallbacks`: `node.step_oom`, then the second way finishing in the same process.
    2. With the fallbacks turned off: `node.oom`, then the engine's retry in a new process.

   It records the seconds of each. It holds when shared usage does not rise during either
   overrun.
5. **The report.** It writes `YYYY-MM-DD-<card>-fit.md` with every number, and says plainly what
   was not measured. The report names the card because it is evidence; nothing in the code reads
   that name.

The owner commits the report; the PR marks AC8 passed only then.
