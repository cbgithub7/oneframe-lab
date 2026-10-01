# 002: Fit to memory

Status: approved (redrafted 2026-09-30, after the owner's review and the research in
[research.md](research.md); amended 2026-09-30 after a second review, with the owner's approval)
Owner approval: 2026-09-30 (spec, and its amendment); 2026-10-01 (plan)

## Problem

How much memory a model needs depends on the model, its checkpoint, its precision, its settings
and its input. The machines people run this on range from no GPU to cards of 80 GB. Today a node
runs on the first device its manifest lists, at full precision, with no check:

- a `cuda` node on a machine without a card fails;
- running out of memory ends the run;
- on Windows, an allocation past the card can spill into shared system memory, so the run crawls
  instead of failing.

The best local AI apps decide when a model loads, from the free memory they measure and an
estimate of what the model needs. They:

- change only what the person left alone;
- give up speed before quality;
- use spare room when there is some;
- retry out-of-memory narrowly.

None asks anyone to run a test ([research.md](research.md)). This spec brings that to the engine,
for any machine that can run a node. The owner's test card is one data point, never the target
(the "Any card that can run it" rule in [AGENTS.md](../../AGENTS.md)).

## Requirements

1. **Each node declares its memory model**, as data in its manifest.
    - **Weights:** the size of its weights for each precision it supports. Where the node offers
      several checkpoints (tiny to large, turbo or full), the size is given for each checkpoint
      choice as well.
    - **Where each precision can run:** the lowest compute capability it needs on a card, and
      whether it runs on the processor. A precision may also state the bytes per value its
      working memory uses, for mixed precision.
    - **Working memory:** a formula over the node's own params and its inputs' numeric `meta`
      (an image's width and height, a view set's number of views, a mesh's face count).
    - **System memory:** what a run needs in system memory besides the device, which is at least
      the weights while they load, and more when weights are offloaded. It defaults to the
      weights' size.
    - **Memory outside torch:** what the node's own extensions allocate on the device.
    - **Changes:** the settings the engine may change to save memory, in order, each marked as
      costing speed only or costing quality, speed-only ones first.
    - **Upgrades:** optionally, speed-only settings the engine may use when there is room (turning
      offloading off, larger chunks).
    - **Time:** optionally, a time figure per kind of device.
    - **Sources:** every figure says where it comes from: a report in this repo naming the card
      and settings it was measured on, or a URL. A figure nobody has measured is written as
      unknown, never guessed.

   The estimate is worked out in the engine without starting the runtime or loading a model
   library. A manifest that gets any of this wrong is refused with every problem named, as today.
   A node without a memory model is not fitted, but still runs within a budget and a cap
   (requirement 5).
2. **The fit.** Before a node's model loads, the engine chooses its device, precision and the
   values of the settings it may change.
    - **Devices:** those of the node's `devices` that this machine has, that the node's runtime
      build supports, and on which the chosen precision can run.
    - **Budgets:** each device's free memory, measured just before the load, less a margin
      (decision 2). A card's fit also checks system memory against its budget, because the
      weights pass through it.
    - **Upgrades:** if the upgrades fit on the first device, they are used.
    - **Speed before quality:** first, each device in order with speed-only changes. Only if none
      fits, each device in order with quality changes as well.
    - **Nothing the person set is changed.** A setting the person chose that does not fit is kept,
      and the node is tried anyway with a warning (decision 3).
    - **A slower device is announced.** When the fit moves to a device slower than the node's
      first (the processor instead of a card), `node.fit` warns that the run may take much
      longer. The warning gives the expected time and says what it is based on: measured on this
      machine, or published, or not known yet. It also gives the faster alternative if there is
      one: the settings that would fit the first device at reduced quality. The person can
      choose that alternative by setting those values.
    - **Nothing fits anywhere:** the node fails before loading, with kind `memory`. The message
      names the smallest need and the free memory on each device, and what would help (closing
      other programs, or a device with at least that much memory).
    - **Unknown estimate:** the fit uses the peak this machine measured for those settings, if
      there is one. Otherwise the node runs at its values on its first device, and the fit says
      the estimate is unknown.
    - **Pure function:** the fit depends only on:
        - the manifest, the params and what the person set;
        - the input sizes;
        - the machine profile and the runtime's target;
        - what this machine has learned (requirement 6);
        - the settings (requirement 11).

      It never reads a card's name.
3. **The node knows its memory.**
    - The context gives it the device, the precision, the fitted values in its params, its budget
      (`ctx.memory_budget_mb`), and the memory still free under its cap
      (`ctx.memory_free_mb()`).
    - `ctx.fallbacks` retries one step a cheaper way in the same process after torch runs out of
      memory. The failed attempt is released before the next way starts. Every way must give the
      same result within rounding, as decision 4 accepts; a way that costs quality belongs in
      the fit's changes instead.
4. **Out of memory, answered narrowly.**
    - Out of memory is recognised in its common forms, each with a test: torch's
      `OutOfMemoryError`, a CUDA, cuBLAS or cuDNN allocation failure, Python's `MemoryError`,
      and any of these as the cause of another error.
    - When a runtime node fails with `oom`, the engine retries it once, in a new process. It
      re-measures free memory, and re-fits from the next change after the ones that failed. With
      an unknown estimate, it takes the next change. Anything the engine itself holds on the
      device is released first.
    - An engine node is retried once in the engine's process.
    - A node with no memory model is not retried, because nothing would change.
    - A second `oom` fails the node. Its message holds both fits and both measured peaks.
    - Any other failure kind ends the run, as today. Stop never leads to a retry.
    - A run the operating system ends for lack of memory is reported as `died`, with a message
      saying it may have run out of system memory.
5. **A cap on the card.** In a torch runtime, the child caps torch's allocator before any node code
   runs.
    - The cap is the smaller of two figures, less the node's memory outside torch:
        - the budget;
        - the free memory the child measures once its CUDA context exists, less a small reserve.
    - An estimate that is too low then becomes an `oom` the retry can answer, not a spill into
      shared memory.
    - Nodes without a memory model are capped too.
    - The `ceiling` event reports the cap and both figures. A runtime without torch runs uncapped,
      and its `ceiling` event says so rather than failing.
    - A run on the processor, in a runtime that has a GPU build, sees no card at all.
6. **Each machine learns.** Every run of a runtime node records, on this machine, its measured peak
   and its seconds beside its estimate. The engine uses them in three ways:
    - **Corrections:** later fits of the same node on the same kind of device scale the working
      memory part of the estimate. A kind of device is the processor, or a card of one compute
      capability, or a card whose capability is unknown. The scale comes from recent runs.
    - **Peaks for unknown estimates:** a node whose estimate is unknown uses the peak measured
      for the same settings.
    - **Times:** the seconds feed the slower-device warning.

   Rules for what is learned:
    - a correction never lowers an estimate from a failed run, or from a single run;
    - it is always taken against the uncorrected estimate;
    - nodes that run in the engine's own process learn nothing, because their peaks cannot be
      told apart.

   What is learned is kept under the data root, written atomically, bounded, with a format
   version. It stops counting when the node's version, its memory model or its runtime's lock
   changes, and a node's learning can be cleared.
7. **Honest results.**
    - Every output records how it was made: the device, the precision, each setting the fit
      changed from its default, and the way each `ctx.fallbacks` step finished. It carries a flag
      when a change cost quality.
    - An output made from a reduced input carries a flag saying so, as trust is inherited.
    - This travels on the output value (its `meta`) and in `node.done`, so the page can show it
      beside the trust label. The fit never changes an output's trust.
8. **The cache never serves less than this machine can make.**
    - Precision, the checkpoint and every output-affecting setting are part of a result's key;
      the device is not (decision 4).
    - If a result at the node's unreduced settings is cached, it is served.
    - Otherwise the engine fits. It then serves the best cached result between the unreduced
      settings and the fitted ones, never one that changes a setting the person chose.
    - If none is cached, the node runs, and its result is stored under the key of the settings it
      actually ran with.
9. **Events.**
    - `node.fit` comes before each attempt's `node.start`. It carries the device, the budgets,
      the estimate (and whether it is known, corrected or measured), the changes made with the
      reason for each, the upgrades, the skipped changes, and any warning.
    - `node.start` carries the attempt number.
    - `node.oom` comes before the engine's retry, and `node.step_oom` before a `ctx.fallbacks`
      retry.
    - `node.done` and `node.failed` carry the fit.
    - The events list in [architecture.md](../../docs/architecture.md) is updated.
10. **Engine methods.**
    - `nodes.fit`: for a node, its params and optional input sizes, the fit on this machine now.
    - `nodes.forget`: clears what this machine learned about a node.
    - `nodes.list` shows the `precision` param the engine adds.

    Neither `nodes.fit` nor `nodes.forget` touches the network, starts a runtime, or loads a model
    library into the engine.
11. **The person's control,** in the engine's settings:
    - the margin, per kind of device;
    - "never reduce quality": the fit uses speed-only changes only, and fails with kind `memory`
      rather than cut quality;
    - "fit off": nodes run at their values on their first device, still capped.

    Both are reported in `node.fit`.

## Acceptance criteria

The test node for these criteria has:
- three precisions: fp32 anywhere, fp16 on cards of compute 6.0 and up, bf16 on cards of compute
  8.0 and up;
- one speed-only setting, a chunk size, with an upgrade;
- one quality setting, a resolution;
- `devices` of `cuda` then `cpu`;
- a working-memory formula over its resolution and chunk size.

The machine profiles used are:

- no GPU, 32 GB of system memory;
- a 4 GB card, compute 5.2, and a 4 GB card, compute 7.5, each with 16 GB of system memory;
- a 6 GB card, compute 7.5;
- an 8 GB card, compute 6.1, with 1.5 GB held by other programs;
- a 12 GB card, compute 8.6;
- a 24 GB card, compute 8.9;
- an 80 GB card, compute 9.0;
- two cards, 8 GB and 24 GB;
- a 2 GB card, compute 6.1, with 4 GB of system memory.

The plan's test table gives the expected fit for each; the owner reviews that table with the plan.

- [ ] AC1: The manifest check refuses each of these, naming every problem at once:
    - a change to an unknown param;
    - a value outside its param's range;
    - a quality change listed before a speed-only one;
    - an upgrade or a `ctx.fallbacks`-style speed change that sets an output-affecting param;
    - an unknown precision, or one with no rule for where it runs;
    - weights keyed by a param that is not a choice;
    - a formula over a name that is neither a param nor an input's `meta` field;
    - a figure with no source.

  A node without a memory model runs at its defaults on the first device this machine has, within
  a budget. Checked by unit tests.
- [ ] AC2: Checked by unit tests of the fit on every profile:
    - the largest cards use the upgrade;
    - middle cards make speed-only changes, on two different kinds of card;
    - a card that cannot fit without losing quality moves to the processor at full quality when
      that fits, and warns that it may be slow, with the reduced alternative;
    - a precision is never chosen where it cannot run;
    - a card's fit also fails when system memory cannot hold the weights;
    - two cards use the card the runtime's plan chose;
    - the 2 GB machine fails before loading, with kind `memory` and a message naming the need and
      the free memory;
    - a setting the graph sets is never changed, on any profile; when it does not fit, the fit
      warns and keeps it;
    - "never reduce quality" and "fit off" change the fits as requirement 11 says;
    - renaming every card in the profiles changes no fit.
- [ ] AC3: Checked by integration tests in the tiny runtime, on a recorded profile, in CI on
  Windows and Linux. The node only reads its fit, so no GPU is needed.
    - A node that raises a CUDA out-of-memory error on its first fit is retried once, in a new
      process, with the next change, and finishes.
    - The events come in order: `node.fit`, `node.start` (1), `node.oom`, `node.fit`,
      `node.start` (2), `node.done`.
    - A second out-of-memory error fails the node with both fits and both peaks, and nothing is
      cached.
    - A failure of another kind, or Stop, leads to no retry.
    - `ctx.fallbacks` moves to its second way in the same process after torch's out-of-memory
      error, with `node.step_oom`, and the failed way's objects are released first.
- [ ] AC4: Checked by tests:
    - after two runs whose working memory measured 1.3 times its estimate, the next fit of that
      node on the same kind of device uses the corrected estimate;
    - one run, or a failed run, never lowers an estimate;
    - a fit on a different kind of device does not use the correction;
    - a node with an unknown estimate uses the peak measured for the same settings;
    - raising the node's version, changing its runtime's lock, or `nodes.forget` drops what was
      learned;
    - the store stays within its bound, and a write interrupted midway leaves the previous file
      readable.
- [ ] AC5: Checked by unit tests:
    - `fp32` and `fp16` results have different keys, and so do two checkpoints;
    - a change of device alone leaves the key the same;
    - a cached result at the unreduced settings is served on a profile whose fit would reduce;
    - a cached reduced result is not served on a profile that can make a better one, nor where
      the graph sets a setting it changed;
    - a reduced result is stored under the key of what it ran with;
    - its value `meta` and `node.done` name the device, precision, changes and quality flag;
    - a result made from it carries the reduced-input flag.
- [ ] AC6: These are classified `oom`:
    - `OutOfMemoryError`;
    - "CUDA error: out of memory";
    - `CUBLAS_STATUS_ALLOC_FAILED` and `CUDNN_STATUS_ALLOC_FAILED`;
    - `MemoryError`;
    - any of these as the cause of another error.

  An unrelated `RuntimeError` is `error`. Checked by unit tests.
- [ ] AC7: Checked by tests:
    - in the tiny runtime, which has no torch, a CUDA fit with a cap runs, and its `ceiling`
      event says the cap was not applied;
    - a processor run in that runtime's GPU build sees no card;
    - `nodes.fit` and `nodes.forget` make no network connection, and load no numpy, Pillow or
      torch into the engine.
- [ ] AC8 (hardware): Proven by a report committed under `specs/002-fit-to-memory/reports/`. It
  proves the mechanism on the one card we have and tunes nothing. On the owner's Windows PC, in
  the torch runtime, a torch test node allocates what its memory model says at three settings:
    - its estimate is within the tolerance the plan sets of the measured peak at each setting;
    - with another program holding memory, the fit makes the changes the budget calls for, and
      the run finishes;
    - an allocation past the cap makes the cap raise `oom`, and the shared GPU memory Windows
      reports for the run does not grow. The overrun is then answered by `ctx.fallbacks` in the
      same process, and, with the fallbacks turned off, by the engine's retry. The report gives
      the seconds of each;
    - the report gives the CUDA context's size, so the margin can be checked against it.

## Out of scope

- Keeping models loaded between runs (spec 004, decision 1).
- Loading part of a model onto the GPU in the engine. A node may offer its own offload setting,
  which the fit can turn on as a speed-only change, or off as an upgrade.
- Weight sizes read from downloaded files, and skipping a checkpoint that is not downloaded
  (spec 003). Until then the manifest states the sizes.
- Memory models for real nodes (spec 005). Each is measured at several settings with
  `npm run bench:fit`, names the card, and is corrected on each machine by requirement 6.
- Reading memory on AMD, Intel and Apple devices. The fit takes a budget from any device the
  machine profile reports; only NVIDIA and the CPU are read today.
- Choosing among several cards per node. The fit uses the card the runtime's plan chose, the one
  with the most memory, even when another card has more free. This is a known limit.
- Setting Windows' "Sysmem Fallback Policy" from code: it would need an undocumented driver ID.
  The docs explain how a person sets it.
- Showing the fit in the page, and estimating a whole run before it starts (workspace UI spec).
  This spec adds no method to the page's allowlist. The workspace UI must write only the params
  a person changed into a recipe, because a param in the recipe counts as set by the person.

## Decisions

Answered by the owner on 2026-09-30:

1. **Keep models loaded: yes, in its own spec** (spec 004, between the model store and the first
   model nodes). Each runtime gets a long-lived worker: kept while idle for a set time, evicted
   when another runtime needs the device, killed on Stop. Every app in the research keeps models
   loaded. The fit in this spec does not depend on it.
2. **The margin: as much as is reasonable.** The larger of 1.5 GiB and 10% of the device's total
   memory, on every OS and for system memory too. It is adjustable per kind of device in the
   settings and reported in `node.fit`.
    - Other apps keep less: ComfyUI 400 MB (600 MB on Windows) plus 0.8 GB for inference,
      Ollama 457 MiB, llama.cpp 1 GiB.
    - This margin also covers the CUDA context, which Accelerate's docs put at 1 to 2 GB, other
      programs growing after the measurement, and estimate error.
    - A bigger margin costs speed or quality a little sooner on small cards; a smaller one risks
      the run crawling in shared memory.
    - The AC8 report measures the context's size, to check the floor.
3. **A setting the person chose that does not fit** is tried anyway, with a warning in
   `node.fit`, as llama.cpp does. The cap turns an overrun into `oom`.
4. **A result made on another device is reused.** Results are saved so the same job is not
   redone. A result made on the CPU is served where the same job asks for a GPU, and the other
   way round: the two differ only in tiny rounding, and the result records the device that made
   it.
5. **Falling to the CPU:** yes, only for nodes that list `cpu`, and labelled. A node that is
   impractical on a CPU does not list it.
6. **Formulas are declared in the manifest,** as coefficients over named params and input sizes.
   Nodes stay data, and the engine never runs a model node's code. A function in the node's
   folder is added later only if a real model's memory cannot be written this way.
7. **Full quality on a slower device before reduced quality on a faster one, and the person is
   told.** When the processor can run a node at full quality but the card cannot, the processor
   is chosen. `node.fit` warns that it may be slow: with the expected time if this machine has
   measured one or the manifest publishes one, and plainly as unknown otherwise. It also gives the
   faster alternative at reduced quality. (Decided 2026-09-30.)
