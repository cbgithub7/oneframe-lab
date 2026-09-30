# 002: Fit to memory

Status: draft (redrafted 2026-09-30, after the owner's review and the research in
[research.md](research.md))
Owner approval: (date, once approved)

## Problem

How much memory a model needs depends on the model, its precision, its settings and its input.
The machines people run this on range from no GPU to cards of 80 GB. Today a node runs on the
first device its manifest lists, at full precision, with no check:

- a `cuda` node on a machine without a card fails;
- running out of memory ends the run;
- on Windows, an allocation past the card can spill into shared system memory, so the run crawls
  instead of failing.

The best local AI apps decide when a model loads, from the free memory they measure and an
estimate of what the model needs. They change only what the person left alone, give up speed
before quality, and retry out-of-memory narrowly. None asks anyone to run a test
([research.md](research.md)). This spec brings that to the engine, for any machine that can run a
node. The owner's test card is one data point, never the target (the "Any card that can run it"
rule in [AGENTS.md](../../AGENTS.md)).

## Requirements

1. **Each node declares its memory model**, as data in its manifest:
    - the precisions it supports, and for each, the size of its weights on the device;
    - its working memory, as a formula over its own params and its inputs' sizes (width and
      height from an input's `meta`);
    - where each figure comes from: a report in this repo naming the card and settings it was
      measured on, or a URL. A figure nobody has measured is written as unknown, never guessed;
    - the settings the engine may change to save memory, in the order it should try them. Each
      is marked as costing speed only or costing quality, and speed-only ones come first.

   The estimate is worked out in the engine without starting the runtime or loading a model
   library. A manifest that gets any of this wrong is refused with every problem named, as today.
   A node without a memory model is not fitted: it runs at its defaults, on the first of its
   `devices` this machine has.
2. **The fit.** Before a node's model loads, the engine chooses its device, precision and the
   values of the settings it may change:
    - **Device:** the first of the node's `devices` that this machine has and the node's runtime
      build supports.
    - **Budget:** the free memory on that device, measured just before the load, less a margin
      (decision 2). The CPU's budget is free system memory less a margin, so a CPU fit is
      checked too.
    - **Search:** start from the node's defaults and the person's settings, then apply the
      declared changes in order until the estimate fits the budget. Nothing the person set is
      changed. A setting the person chose that does not fit is kept, and the node is tried with a
      warning (decision 3).
    - **Fallback:** if nothing fits on the first device, try the next device the node lists.
    - **Nothing fits anywhere:** the node fails before loading, with kind `memory`. The message
      names the smallest need, the free memory on each device, and what would help (closing
      other programs, or a device with at least that much memory).
    - **Unknown estimate:** the node runs at its defaults, and the fit says the estimate is
      unknown.
    - **Pure function:** the fit depends only on the manifest, the params, the input sizes, the
      machine profile, the runtime status and the local corrections (requirement 6). It never
      reads a card's name.
3. **The node knows its budget.** The context gives it the device, the precision, the fitted
   values in its params, and `ctx.memory_budget_mb`. A node can use the budget to size its own
   tiles, or to retry one step in tiles after running out of memory, without leaving the process.
4. **Out of memory, answered narrowly.**
    - Out of memory is recognised in its common forms, each with a test: torch's
      `OutOfMemoryError`, a CUDA or cuBLAS allocation failure reported as a `RuntimeError`, and
      Python's `MemoryError`.
    - When a node fails with `oom`, the engine retries it once, in a new process. It re-measures
      free memory and re-fits from the next change after the ones that failed. Anything the
      engine itself holds on the device is released first.
    - A second `oom` fails the node. Its message holds both fits and both measured peaks.
    - Any other failure kind ends the run, as today. Stop never leads to a retry.
5. **A cap on the card.** In a torch runtime, the child process caps torch's allocator at the
   budget before any node code runs. An estimate that is too low then becomes an `oom` the retry
   can answer, not a spill into shared memory. The `ceiling` event reports the cap. A runtime
   without torch runs uncapped, and its `ceiling` event says so rather than failing.
6. **Each machine corrects the estimates.** Every run records its measured peak beside its
   estimate (the run record already keeps the peak).
    - On this machine, later fits of the same node on the same kind of device (processor, or a
      card of the same compute capability) scale the estimate by the largest ratio of measured
      peak to estimate seen so far.
    - This is how the app learns about cards nobody here has tested.
    - The corrections are small, kept under the data root, written atomically, bounded, and carry
      a format version.
    - A correction stops counting when the node's version or its memory model changes, and a
      node's corrections can be cleared.
7. **Honest results.**
    - Every output records how it was made: the device, the precision, and each setting the fit
      changed from its default, with a flag when a change cost quality.
    - This travels on the output value (its `meta`) and in `node.done`, so the page can show it
      beside the trust label. The fit never changes an output's trust.
8. **The cache.**
    - Precision and every output-affecting setting are part of a result's key; the device is not
      (open question 4).
    - Before fitting, the engine looks for a cached result at the node's unreduced settings, then
      at each reduction in order, and serves the first it finds.
    - A result made on a bigger machine, or on a day with more free memory, is therefore reused
      instead of being made again at lower quality.
9. **Events.** `node.fit` comes before the load, with the device, budget, estimate (and whether it
   is known or corrected), and the changes made with the reason for each. `node.oom` comes before
   the retry. `node.done` and `node.failed` carry the fit. The events list in
   [architecture.md](../../docs/architecture.md) is updated.
10. **Engine methods.**
    - `nodes.fit`: for a node, its params and optional input sizes, the fit on this machine now.
    - `nodes.forget`: clears a node's corrections.

    Neither touches the network, starts a runtime, or loads a model library into the engine.

## Acceptance criteria

The test node for these criteria has two precisions, one speed-only setting (a chunk size), one
quality setting (a resolution), `devices` of `cuda` then `cpu`, and a working-memory formula over
its resolution and chunk size. The machine profiles used are:

- no GPU, 32 GB of system memory;
- a 4 GB card, compute 5.2;
- an 8 GB card, compute 6.1, with 1.5 GB held by other programs;
- a 12 GB card, compute 8.6;
- a 24 GB card, compute 8.9;
- an 80 GB card, compute 9.0 (its margin is 8 GB, 10% of the card);
- two cards, 8 GB and 24 GB;
- a 2 GB card with 4 GB of system memory.

The plan's test table gives the expected fit for each; the owner reviews that table with the plan.

- [ ] AC1: The manifest check refuses each of these, naming every problem at once:
    - a change to an unknown param;
    - a value outside its param's range;
    - a quality change listed before a speed-only one;
    - an unknown precision;
    - a formula over a name that is neither a param nor an input size;
    - a figure with no source.

  A node without a memory model runs at its defaults on the first device this machine has.
  Checked by unit tests.
- [ ] AC2: Checked by unit tests of the fit on every profile:
    - the largest cards run at the defaults;
    - middle cards make speed-only changes;
    - small cards make quality changes, and say so;
    - no GPU falls to the CPU;
    - two cards use the card the runtime's plan chose;
    - the 2 GB machine fails before loading, with kind `memory` and a message naming the need and
      the free memory;
    - a setting the graph sets is never changed, on any profile; when it does not fit, the fit
      warns and keeps it;
    - renaming every card in the profiles changes no fit.
- [ ] AC3: Checked by integration tests in the tiny runtime, on a recorded profile, in CI on
  Windows and Linux. The node only reads its fit, so no GPU is needed.
    - A node that raises a CUDA out-of-memory error on its first fit is retried once, in a new
      process, with the next change, and finishes.
    - The events come in order: `node.fit`, `node.oom`, `node.fit`, `node.done`.
    - A second out-of-memory error fails the node with both fits and both peaks, and nothing is
      cached.
    - A failure of another kind, or Stop, leads to no retry.
- [ ] AC4: Checked by tests:
    - after a run whose measured peak was 1.3 times its estimate, the next fit of that node on the
      same kind of device uses the corrected estimate;
    - a fit on a different kind of device does not;
    - raising the node's version, or `nodes.forget`, drops the correction;
    - the corrections file stays within its bound, and a write interrupted midway leaves the
      previous file readable.
- [ ] AC5: Checked by unit tests:
    - `fp32` and `fp16` results have different keys;
    - a change of device alone leaves the key the same;
    - a cached result at the unreduced settings is served on a profile whose fit would reduce;
    - a reduced result's value `meta` and `node.done` name the device, the precision, the changes,
      and the quality flag.
- [ ] AC6: `OutOfMemoryError`, "CUDA error: out of memory", `CUBLAS_STATUS_ALLOC_FAILED` and
  `MemoryError` are classified `oom`; an unrelated `RuntimeError` is `error`. Checked by unit
  tests.
- [ ] AC7: In the tiny runtime, which has no torch, a CUDA fit with a cap runs, and its `ceiling`
  event says the cap was not applied. `nodes.fit` and `nodes.forget` make no network connection
  and load no numpy, Pillow or torch into the engine. Checked by tests.
- [ ] AC8 (hardware): Proven by a report committed under `specs/002-fit-to-memory/reports/`. It
  proves the mechanism on the one card we have and tunes nothing. On the owner's Windows PC, in
  the torch runtime, a torch test node allocates what its memory model says at three settings:
    - its estimate is within the tolerance the plan sets of the measured peak at each setting;
    - with other programs holding memory, the fit makes the changes the budget calls for, and the
      run finishes;
    - forcing a too-small estimate makes the cap raise `oom`, the shared GPU memory Windows
      reports does not grow, and the retry finishes;
    - the report gives the CUDA context's size, so the margin can be checked against it.

## Out of scope

- Keeping models loaded between runs (spec 004, decision 1).
- Loading part of a model onto the GPU in the engine. A node may offer its own offload setting,
  which the fit can turn on as a speed-only change.
- Weight sizes read from downloaded files (spec 003). Until then the manifest states them.
- Memory models for real nodes (spec 005). Each is measured at several settings, names the card,
  and is corrected on each machine by requirement 6.
- Reading memory on AMD, Intel and Apple devices. The fit takes a budget from any device the
  machine profile reports; only NVIDIA and the CPU are read today.
- Setting Windows' "Sysmem Fallback Policy" from code: it would need an undocumented driver ID.
- Showing the fit in the page, and estimating a whole run before it starts (workspace UI spec).
  This spec adds no method to the page's allowlist.

## Decisions

Answered by the owner on 2026-09-30:

1. **Keep models loaded: yes, in its own spec** (spec 004, between the model store and the first
   model nodes). Each runtime gets a long-lived worker: kept while idle for a set time, evicted
   when another runtime needs the device, killed on Stop. Every app in the research keeps models
   loaded. The fit in this spec does not depend on it.
2. **The margin: as much as is reasonable.** The larger of 1.5 GiB and 10% of the device's total
   memory, on every OS. It is adjustable in the settings and reported in `node.fit`.
    - Other apps keep less: ComfyUI 400 MB (600 MB on Windows) plus 0.8 GB for inference,
      Ollama 457 MiB, llama.cpp 1 GiB.
    - This margin also covers the CUDA context, which Accelerate's docs put at 1 to 2 GB, other
      programs growing after the measurement, and estimate error.
    - A bigger margin costs speed or quality a little sooner on small cards; a smaller one risks
      the run crawling in shared memory.
    - The AC8 report measures the context's size, to check the floor.
3. **A setting the person chose that does not fit** is tried anyway, with a warning in
   `node.fit`, as llama.cpp does. The cap turns an overrun into `oom`.
5. **Falling to the CPU:** yes, only for nodes that list `cpu`, and labelled. A node that is
   impractical on a CPU does not list it.
6. **Formulas are declared in the manifest,** as coefficients over named params and input sizes.
   Nodes stay data, and the engine never runs a model node's code. A function in the node's
   folder is added later only if a real model's memory cannot be written this way.

## Open questions

4. **Reuse a result made on another device?** Results are saved so the same job is not redone.
   If a result was made on the CPU and the same job later runs where a GPU is free, should the
   saved result be reused, or made again on the GPU? The two differ only in tiny rounding.

   *Recommended:* reuse it. It is instant, and the result still says which device made it.
