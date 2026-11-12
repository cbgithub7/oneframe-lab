# 023: Several runs at once, a slot per device

Status: draft
Owner approval: (date, once approved)

The last of five specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), [020](../020-structured-params/spec.md) (data a person authors),
[021](../021-collections/spec.md) (collections and "for each"),
[022](../022-run-to-a-node/spec.md) (a run up to a node) and this one. From the
[review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 1, item 2. It lands after 021
(its events carry an item) and 022 (Run to here is what a person presses while a run goes).

## Problem

Only one run goes at a time: a second is refused `busy` (`server.py`). A click on a photo cannot
be answered while a long run goes, though it may need only the processor, or the card for a second
between two long steps. The Improve loop (step 6) brings runs of many minutes.

## Requirements

1. **Several runs go at once** (open question 1). Each attempt holds a slot while it runs: one
   attempt at a time on each card, one on the processor, and one in the engine's own process. A run
   on the processor and one on a card go side by side.
2. **A step that must wait says so,** naming the run and step ahead of it, and starts when the slot
   is free, first come first served.
3. **A step is fitted only once it holds its slot,** so its fit reads the memory it will have: a
   step behind a run that fills the card waits for the card, and never fails `memory`, moves to
   the processor or loses quality because of what that run holds.
4. **The same work asked for twice is done once:** a step another run is making waits for it and
   is answered from the cache.
5. **Stop ends one run** (`run.stop {run}`) or, with no run named, every run and every run still
   queued. A waiting step stops at once; the other runs go on.
6. **What guards a run guards every run:** a runtime any run uses is neither removed nor installed
   over; a file any run reads is not removed; the engine's close stops them all. Each run still
   sends its ending after its place is free.
7. **The number of runs has a bound,** kept in code; a run beyond it is refused `graph`/`busy`.
8. **The page lets a person run while a run goes** (open question 2): Run and Run to here stay
   enabled, and a card whose step waits for a device says so in words, and behind what.

## Acceptance criteria

- [ ] AC1: `test_two_runs_go_at_once_on_different_devices` (`test_server_and_nodes.py`): with test
  nodes that hold until released, a run whose step is on the processor and one whose step is on a
  card have both said `node.start` before either is released. Fails today: `busy`.
- [ ] AC2: `test_a_step_waits_for_its_device_in_turn` (`test_scheduler.py`): of three runs with a
  step on one device, the second and third say `node.queued`, naming the run and step ahead, and
  start in the order they asked, each after the one before it has ended.
- [ ] AC3: `test_a_step_is_fitted_only_once_it_holds_its_slot` (`test_scheduler.py`): with a machine
  that reports its card full while a first run's step holds it, a second run's step for the card
  says `node.queued` and no `node.fit` until the first has ended, then one `node.fit`, on the card
  at its own values, and ends done.
- [ ] AC4: `test_stop_ends_one_run_and_leaves_the_others` (`test_server_and_nodes.py`): stopping,
  by its id, a run whose step waits ends it `run.stopped` within the test's deadline, while the
  run ahead of it goes on to `run.done`.
- [ ] AC5: `test_the_same_work_in_two_runs_is_done_once` (`test_scheduler.py`): two runs of one
  graph started together call the node's code once; the second run's step is `node.cached`.
- [ ] AC6: `test_a_runtime_any_run_uses_is_not_removed` (`test_runtime_server.py`): with two runs
  going, removing the runtime only the second uses is refused `in_use`, naming that run, and is
  allowed once it has ended.
- [ ] AC7: `test_a_file_any_run_reads_is_not_removed` (`test_model_runs.py`): a file a first run
  reads is still listed as in use after a second run starts and after that second run ends, and
  no longer once the first has ended.
- [ ] AC8: "a second run waits for the device and says so" (`tests/e2e/first-page.test.js`, in CI):
  while a first run's step holds the engine's slot, Run to here on another card shows that card
  queued behind the first, in words; when the first ends, the card ends done.

## Design

**Slots** (`oneframe/slots.py`, new; one set for the engine, given to the `Scheduler`). A slot is
named by what an attempt runs on: a card's index, the processor, or the engine's process for a node
that runs there. `_step` checks the cache first, as today. Then it takes a slot before it fits: the
card's when the node can use a card on this machine (what `_Context.cards` says today), else the
processor's, or the engine's for an engine node. Holding it, it fits; a fit that puts a card's node
on the processor lets the card go and takes the processor's slot, once, and fits again there. The
slot is held to the attempt's end, the retry after `oom` included. A step that must wait says
`node.queued`, and its wait watches the run's stop. With 021, each item's run takes its turn, so a
click's run slips between the items of a long for each.

**The same work** (`scheduler.py`): the scheduler keeps, across its runs, the key of each step
being made, at the step's own values (the key `_step` looks up first). A step that finds its key
there waits for that step to end, then takes the normal path: the cache, or its own attempt if the
other failed.

**The server** (`server.py`): `_run` becomes the runs by id, each with its stop and the runtimes it
uses; `graph.run` refuses only past `MAX_RUNS` (8); `run.stop` sets one stop or all, and takes the
queued `graph.run` requests off the queue only when it names no run. `_refuse_if_running` and
`shutdown` read every run. The list of files a process is reading (`models/runs.py`, `reading`) is
one file a process, which a second run would overwrite: it becomes the union of what this
process's runs read, written under a lock as each starts and ends, and a run still leaves it before
its ending is sent. `graph`/`busy` in `errors.py` and architecture.md's table reads "too many
runs are going".

**The protocol** (`contracts/protocol.json`): `node.queued` (`step`, `node`, `device`, `behind`: a
run and a step, and with 021 the `item` and `of` of each) is a new event; `run.stop`'s answer lists
the runs it stopped.

**The page** (`main.js`, `run-view.js`): Run and Run to here stay enabled while a run goes. The run
view keeps each step's state from the newest run that named it in its `run.start`, so an older
run's late events do not overwrite a newer one's. `node.queued` is a phase of its own, `queued`
("Queued for the card, behind Depth"); the page's `waiting` keeps meaning "not reached yet". Stop
stops every run this page started (open question 2).

**Docs:** architecture.md (Events: `node.queued`, several runs; the server's opening lines; the
failures table), and the handoff.

Risks:

- **System memory is shared by the slots, and nothing reserves it.** Two fits in the same second,
  one for the card and one for the processor, can both count the same free memory; the later load
  then ends `oom` or `died`, and Run again answers it. Spec 004's accounting (`held_mb`) is what
  closes this; open question 1 names it.
- Two attempts at once in one process touch shared state: engine nodes take the engine's slot, so
  node code never overlaps; the cache's commit, the learned store and the journals are already
  safe for two writers, which a test of two runs holds.
- A slot held by a step that hangs: Stop ends its process tree, as today.

## Tests

- Added: the tests the criteria name; in `test_server_and_nodes.py`,
  `test_runs_beyond_the_bound_are_refused`, which holds requirement 7, and
  `test_a_stop_that_names_a_run_leaves_queued_runs`; "a newer run's state is not overwritten by an
  older run's events" (`tests/app/page.test.js`).
- Changed: `test_a_stop_cancels_a_queued_run` (`test_server_and_nodes.py`) stops with no run named,
  as the page's Stop does.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run engine:check` or `npm run e2e`, in CI on Windows and Ubuntu.
The engine's tests use nodes that hold until released, never a sleep, and every wait has a deadline
(spec 014); AC8 uses the e2e fixture that waits its seconds, marked as the evidence it is.

Unverified until a real machine runs it: two real models side by side, one on the processor and
one on a card. The PR puts it under "Waiting on a local session" in the handoff: on the GTX 1070,
`npm start` from a terminal outside Claude desktop's package, the shipped graph with Depth Pro set
to fp32 (the processor) and MoGe-3 on the card, each started with Run to here; passing is both
ending done, with their peaks and the free system memory before each, in
`specs/023-runs-at-once/reports/<date>-local.md`.

## Decisions taken

- A slot per device, not memory shared on a card: two attempts on one card would need the fit to
  count what the other will still take, which spec 004 brings.
- The slot before the fit: a fit made while another run fills the card would fail or shrink a step
  that only has to wait.
- First come first served: a run cannot starve, and no priority has to be explained.
- Engine nodes take a slot of their own, one at a time: nothing in the engine's process was
  written to run beside itself, and they are short.
- A bound of 8 runs: each is a thread, and a page that loops must not start a thousand.

## Out of scope

- Two attempts on one card at once, a warm worker's memory, and system memory reserved between
  slots: spec 004.
- A queue the person can see and reorder, and a Stop on each run: the editor, step 7.
- More than one card used at once by choice: the fit picks the card today; the slot only follows.

## Open questions (answered by the owner, 2026-11-12: every one as recommended)

1. **Run while a run goes.** Today the second is refused. Recommended: it starts, and each of its
   steps waits its turn for its device, saying what it waits behind. A step for the card still
   waits for the card; and two runs that start together on the card and the processor can, rarely,
   both count the same free system memory, so one fails and is run again, until spec 004. The
   other way keeps one run and queues whole runs: simpler, with neither risk, and a click then
   waits for a long run's end.
2. **Stop, with several runs.** Recommended: the page's one Stop stops every run it started, and
   a card shows the newest run that holds its step. A Stop for each run needs a list of runs in
   the page, which is the editor's (step 7).
