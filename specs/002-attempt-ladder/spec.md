# 002: Attempt ladder

Status: draft
Owner approval: (date, once approved)

## Problem

A model that fits on one card runs out of memory on another. The usual ways round it are known
per model (half precision, a smaller working resolution, tiling, offloading), but nothing writes
them down where the engine can use them. Today a node runs once, on the first device its manifest
lists, at full precision: a `cuda` node on a machine without a card fails, and an out-of-memory
error ends the run. No ceiling is set on the card, so on Windows an allocation past the card's
memory can spill into shared system memory instead of failing, and the desktop crawls. Nothing
records what worked on this machine, so the same failure happens on every run.

## Requirements

1. **Arrangements are data.** A node's manifest may list `arrangements`, best quality first. Each
   one has:
    - an id, unique within the node;
    - a device, one of the node's `devices`;
    - a precision: `fp32`, `fp16` or `bf16`;
    - settings: values for the node's own params (a working resolution, a tile size, offloading),
      each checked against that param's type, range and choices;
    - optionally, the lowest compute capability it needs (bf16 and some attention kernels need
      8.0);
    - for a GPU arrangement, its VRAM figure and where the figure comes from: a URL, or a report
      in this repo. A figure nobody has published is written as unknown, never guessed.
    - optionally, a time figure and its source.

   A manifest that gets any of this wrong is refused with every problem named, as today. A node
   without `arrangements` has one per device in its `devices` list, in that order, at `fp32`, with
   no settings. Nothing outside a node's folder names a model or an arrangement.
2. **The ladder for this machine.** Before a node runs, the engine builds its ladder: the node's
   arrangements in order, each marked "try" or "skipped" with the reason. An arrangement is
   skipped when:
    - its device is not here: a `cuda` arrangement on a machine with no usable NVIDIA card, or
      when the node's runtime is installed as its CPU build;
    - the card's compute capability is below what the arrangement needs;
    - it would change a param the graph sets explicitly (open question 3);
    - this machine has seen it run out of memory under a ceiling at least as high as the current
      one (requirement 5).

   The ladder is a pure function of the manifest, the params, the machine profile, the runtime's
   status and the observations, so it is unit-tested like the runtime plan.
3. **Walking the ladder.** The scheduler tries the arrangements marked "try", in order:
    - Each attempt of a runtime node is a fresh child process, so a failed attempt leaves nothing
      on the card.
    - Only `oom` moves to the next arrangement. Any other failure kind ends the run, as today.
    - Stop during an attempt stops the run, and no further attempt starts.
    - When no arrangement is left, the node fails with kind `oom`, and the message lists every
      arrangement and what happened to it (skipped and why, or ran out of memory at what peak).

   The node learns its arrangement from its context: `ctx.device`, `ctx.precision` and
   `ctx.arrangement` (the id), with the arrangement's settings applied to `ctx.params`.
4. **A ceiling on the card.** Every attempt on a CUDA device runs under an allocator ceiling, so
   running out of memory is an `oom` error the ladder can act on, never a spill into shared
   system memory.
    - The ceiling is worked out before each attempt, for the card the runtime's plan chose, from
      the machine profile (open question 1).
    - The child applies it before any node code runs, and reports it in the `ceiling` event.
    - It caps torch's allocator. A runtime without torch runs without a ceiling and its `ceiling`
      event says so; the attempt does not fail for that reason.
    - Out of memory is recognised in its common forms, each with a test: torch's
      `OutOfMemoryError`, a CUDA or cuBLAS allocation failure reported as a `RuntimeError`, and
      Python's `MemoryError` on the CPU.
5. **Observations per machine.** After each attempt, the engine records what happened on this
   machine: the node's id and version, the arrangement's id and a hash of its content, the
   runtime build, the card (compute capability and total VRAM, never its name), the ceiling, the
   outcome (`done` or `oom`), the peak VRAM, the seconds, and the date.
    - They are kept under the data root, written atomically, bounded in size (the oldest go
      first), and carry a format version.
    - They change the ladder. An arrangement that ran out of memory under a ceiling at least as
      high as the current one is skipped, with the date it happened. One that finished shows its
      measured peak and time in place of the published figures. So a run starts from the best
      arrangement not known to fail here.
    - An observation stops counting when the node's version, the arrangement's content, the
      runtime build or its lock, or the card changes.
    - A node's observations can be cleared.
6. **The cache follows the arrangement.**
    - A result's key includes the arrangement's precision and its output-affecting settings, so
      an `fp16` result is never served for an `fp32` request. The device and speed-only settings
      are not in the key (open question 4).
    - A step is served from the cache when an arrangement at or above the first one its ladder
      would try has a cached result; the best of those is used. While walking down after an
      `oom`, a lower arrangement's cached result is used instead of running it. A cached result
      from a lower arrangement is never used before the better ones have been tried.
    - The record kept with a result names the arrangement that made it: id, device, precision and
      settings. An arrangement never changes an output's trust.
7. **Choosing by hand.** A graph node may name one arrangement. Then only that one is tried; if it
   is skipped here or runs out of memory, the node fails with that reason. This is how two
   arrangements are compared side by side.
8. **Events.** Each attempt is announced before it starts (`node.attempt`: the arrangement, its
   place in the ladder, the ceiling). An `oom` that moves down the ladder is reported with its
   peak VRAM (`node.oom`) before the next attempt starts. `node.done` and `node.failed` name the
   arrangement and list the attempts. The existing events keep their meaning, and the events list
   in `docs/architecture.md` is updated.
9. **Engine methods.**
    - `nodes.ladder`: for a node and optional params, its ladder on this machine. Each
      arrangement is shown as try or skipped with the reason, with its published and observed
      figures and the ceiling.
    - `nodes.forget`: clears a node's observations.

   Neither touches the network, starts a runtime, or loads a model library into the engine.

## Acceptance criteria

The test node for AC2 to AC7 has four arrangements, best first: `a` cuda bf16 needing compute
8.0; `b` cuda fp32 at resolution 1024; `c` cuda fp16 at resolution 768; `d` cpu fp32 at
resolution 768. It raises a CUDA out-of-memory error unless its resolution is at most 768.

- [ ] AC1: The manifest check refuses each of these, naming every problem at once: a setting for
  an unknown param; a value outside its param's range; a device not in `devices`; an unknown
  precision; a duplicate id; a GPU arrangement with no VRAM figure or no source. A node without
  `arrangements` gets one per listed device at `fp32`. Checked by unit tests.
- [ ] AC2: Unit tests check the ladder for the test node in each of these cases:
    - compute 6.1 with the `cu126` build installed: `a` skipped (needs compute 8.0); `b`, `c` and
      `d` to try;
    - no NVIDIA card, or the runtime's CPU build installed: `a`, `b` and `c` skipped; `d` to try;
    - the graph sets resolution 1024: `c` and `d` skipped, naming the param;
    - `b` ran out of memory under a 7000 MB ceiling: skipped under a 7000 MB ceiling, with the
      date; tried again under 7500 MB.
- [ ] AC3: Run through the scheduler on a recorded profile of a compute 7.5 card (so `a` is
  skipped), the test node tries `b`, which runs out of memory, then `c`, which finishes. The node
  only reads its arrangement, so no GPU is needed. Checked by an integration test in the tiny
  runtime's `cu130` build, in CI on Windows and Linux:
    - the events come in this order: `node.start`, `node.attempt` (b), `node.oom` (b),
      `node.attempt` (c), `node.done`;
    - `node.done` names `c` and lists both attempts;
    - the cache record names `c`;
    - the two attempts ran in different processes.
- [ ] AC4: A failure of another kind (`missing`, `node`) in the first attempt ends the run with
  that kind, and no second attempt starts. Stop during the first attempt ends the run as stopped,
  and no second attempt starts. Checked by tests.
- [ ] AC5: When every arrangement runs out of memory, the node fails with kind `oom`, the message
  names each arrangement and its outcome, and nothing is cached. Checked by a test.
- [ ] AC6: Checked by tests:
    - after AC3's run, a run on a new input starts at `c` without trying `b`;
    - `nodes.ladder` shows `b` skipped with the date;
    - after `nodes.forget`, `b` is tried again;
    - raising the node's version makes the observation stop counting;
    - the observations file stays within its bound when more attempts are recorded than it
      holds;
    - a write interrupted midway leaves the previous file readable.
- [ ] AC7: Checked by unit tests:
    - `fp32` and `fp16` results of the same inputs have different keys;
    - changing only the device, or only a speed-only setting, leaves the key the same;
    - a run whose ladder starts at `b` reuses a cached result from `a`;
    - it does not reuse one from `c` without first trying `b`.
- [ ] AC8: Checked by unit tests and one child-process test:
    - The ceiling is worked out as open question 1 decides, from recorded profiles: an 8 GB card
      with little used, an 8 GB card with the desktop holding memory, and a 24 GB card.
    - Only CUDA attempts carry one.
    - In the tiny runtime, which has no torch, a CUDA attempt with a ceiling runs and its
      `ceiling` event says the ceiling was not applied.
- [ ] AC9: `OutOfMemoryError`, "CUDA error: out of memory", `CUBLAS_STATUS_ALLOC_FAILED` and
  `MemoryError` are classified `oom`, and an unrelated `RuntimeError` is `error`. Checked by unit
  tests.
- [ ] AC10: `nodes.ladder` and `nodes.forget` make no network connection, and importing and
  calling them loads no numpy, Pillow or torch into the engine. Checked by tests with sockets
  blocked.
- [ ] AC11 (hardware): Proven by a report committed under `specs/002-attempt-ladder/reports/`.
  On the owner's Windows PC (GTX 1070, 8 GB, compute 6.1), in the torch runtime's `cu126` build,
  a test node runs whose first arrangement allocates more than the ceiling and whose second fits:
    - the first attempt fails with `oom` from the ceiling, and the shared GPU memory Windows
      reports does not grow during it;
    - the second attempt finishes on the card;
    - the report gives the ceiling, the free VRAM before each attempt, and the seconds and peak
      VRAM of each attempt;
    - a second run starts at the second arrangement.

## Out of scope

- Model weights and Download (spec 003).
- Real model nodes and their arrangements (spec 004): Depth Pro, MoGe-2 and the rest bring their
  own, each VRAM figure with its source.
- Estimating a whole run's time and peak memory before it starts, and showing the ladder in the
  page. That is the workspace UI spec; this spec adds no method to the page's allowlist.
- Keeping a model loaded between nodes, or parking it in system memory. Every attempt is a child
  process, as now.
- Several cards at once, and two nodes running at once. The card is the one the runtime's plan
  chose.
- Ceilings for allocators other than torch's (ONNX Runtime, JAX), and for system memory.
- Offloading chosen by the engine. An arrangement's settings may turn a node's own offload param
  on; the engine does nothing of its own.

## Open questions

Answered by the owner before approval. Each has a recommendation.

1. **The ceiling.** How much of the card may an attempt use?
    - a. The card's total less a fixed reserve (say 10%). Stable from run to run, so observations
      compare cleanly. But if other programs hold memory, the card can still spill.
    - b. The free memory at the start of each attempt, read with nvidia-smi, less a margin for
      the CUDA context torch creates outside its allocator. Tracks what is really free, but moves
      from run to run, so an arrangement can fit one day and not the next.
    - c. The smaller of a and b.

   *Recommended:* c, with a 512 MB margin that the AC11 report checks against the context size it
   measures, and a setting to override the ceiling by hand.
2. **Published figures.** An arrangement whose published VRAM figure is above the ceiling:
    - a. Try it once. The out-of-memory is recorded, and later runs skip it. Costs one failed
      model load per machine.
    - b. Skip it on the published figure, unless an observation here says it fit.
    - c. Skip it only when the figure is above the card's total memory; try anything closer.

   *Recommended:* c. Hopeless attempts are not made, and close calls are measured rather than
   guessed.
3. **Params a person sets.** When the graph sets a param explicitly and an arrangement would
   change it:
    - a. Skip that arrangement. What a person sets is never overridden.
    - b. The arrangement wins, and the attempt event says so.

   *Recommended:* a.
4. **The device in the cache key.** Should a CPU `fp32` result be served where a CUDA `fp32` one is
   asked for? The two are close but not bit-identical.

   *Recommended:* yes. Leave the device out of the key; the record names it.
5. **Falling to the CPU.** Should the ladder move to a CPU arrangement without asking, although it
   can take many times longer?

   *Recommended:* yes. A node that is impractical on a CPU lists no CPU arrangement. The attempt
   event names the device, so the page can show it, and Stop is always there.
6. **What "re-order" means.** The handoff says observations re-order arrangements. This draft reads
   that as: arrangements known to run out of memory here are skipped, so the ladder starts at the
   best one that fits. The order stays by quality, and timings are recorded and shown but never
   change the order. Is that the intent?
