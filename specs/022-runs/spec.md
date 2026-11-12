# 022: Runs up to a node, and several at once

Status: draft
Owner approval: (date, once approved)

The last of four specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), [020](../020-structured-params/spec.md) (data a person authors),
[021](../021-collections/spec.md) (collections and "for each") and this one. From the
[review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 1, item 2, and the
[review of 2026-11-07](../../docs/reviews/2026-11-07.md), section 2, finding 3 (one graph walk).

## Problem

- **A run is always the whole graph.** To see the masks a click gives, a person runs everything
  after the segmenter too; and a graph with one input still unconnected cannot run at all, so
  nothing can be looked at while a graph is being built.
- **Only one run goes at a time.** A second is refused `busy` (`server.py`). A click on a photo
  cannot be answered while a long run goes, though it may need only the processor, or the card for
  a second between two long steps.
- **The graph is walked in three places:** `graph.py` (`_reaches`, for `fits`), and
  `bench_fit.py` twice (`upstream_of`, `part_of`).

## Requirements

1. **A run may name the steps it is for** (`graph.run {graph, upto}`): those steps and what they
   depend on run, and nothing else is planned, so a problem elsewhere in the graph does not refuse
   it. Without `upto` the whole graph runs, as today.
2. **Several runs go at once** (open question 1). Each attempt holds its device's slot while it
   runs: one attempt at a time on each card, one on the processor, and one in the engine's own
   process. A run on the processor and one on a card go side by side.
3. **A step that waits says so,** naming the run and step it waits behind, and starts when the
   slot is free, first come first served. It is fitted once it holds the slot, so the fit reads
   the memory the step will have.
4. **The same work asked for twice is done once:** a step whose key another run is making waits
   for it and is answered from the cache.
5. **Stop ends one run** (`run.stop {run}`) or, with no run named, every run. A waiting step stops
   at once; the other runs go on.
6. **What guards a run guards every run:** a runtime any run uses is neither removed nor installed
   over, and the engine's close stops them all. Each run still sends its ending after its place is
   free.
7. **The number of runs has a bound,** kept in code; a run beyond it is refused `graph`/`busy`.
8. **The page offers "Run to here"** on each card, and Run while another run goes. A card shows
   the newest run that holds its step, a waiting step as waiting and behind what. Both controls
   are reachable from the keyboard.
9. **One graph walk,** in `graph.py`, which `fits`, `upto` and `bench:fit --graph` all use.

## Acceptance criteria

- [ ] AC1: `test_a_run_up_to_a_step_runs_only_what_it_needs` (`test_scheduler.py`): in a graph of
  two branches and a third node with an unconnected input, a run up to one branch's last step
  starts with that branch's steps as its order, ends done, and never names the others; a step in
  `upto` that the graph does not have is refused with kind `graph`. Fails today: no `upto`.
- [ ] AC2: `test_two_runs_go_at_once_on_different_devices` (`test_server_and_nodes.py`): with test
  nodes that hold until released, a run whose step is on the processor and one whose step is on a
  card have both said `node.start` before either is released. Fails today: `busy`.
- [ ] AC3: `test_a_step_waits_for_its_device_in_turn` (`test_scheduler.py`): of three runs with a
  step on one device, the second and third say `node.waiting`, naming the run and step ahead, and
  start in the order they asked, each after the one before it has ended.
- [ ] AC4: `test_stop_ends_one_run_and_leaves_the_others` (`test_server_and_nodes.py`): stopping a
  run whose step waits ends it `run.stopped` within the test's deadline, while the run it waited
  behind goes on to `run.done`; `run.stop` with no run ends every run.
- [ ] AC5: `test_the_same_work_in_two_runs_is_done_once` (`test_scheduler.py`): two runs of one
  graph started together call the node's code once; the second run's step is `node.cached`.
- [ ] AC6: `test_a_runtime_any_run_uses_is_not_removed` (`test_runtime_server.py`): with two runs
  going, removing the runtime only the second uses is refused `in_use`, naming that run, and is
  allowed once it has ended.
- [ ] AC7: `test_runs_beyond_the_bound_are_refused` (`test_server_and_nodes.py`): with the bound
  set to two, a third run is refused `graph`/`busy`, and is accepted once one has ended.
- [ ] AC8: "Run to here runs up to a card, beside another run" (`tests/e2e/first-page.test.js`, in
  CI): from the keyboard, Run to here on a card ends with that card done and a later card never
  started; started while a first run holds the device, the card says it waits, then ends done.

## Design

**The walk** (`graph.py`): `_reaches` becomes public, `reaches(graph, start, backwards)`, and
`Graph.part(keys)` gives the nodes in `keys` and the edges between them. `bench_fit.py`'s
`upstream_of` and `part_of` go, their callers using these. `plan(graph, registry, imports, upto)`
plans `graph.part(...)` of what `upto` reaches backwards; `graph.validate` takes `upto` too.

**Slots** (`oneframe/slots.py`, new; given to the `Scheduler`, one for the engine). A slot is named
by what the attempt runs on: a card's index, the processor, or the engine's process for a node that
runs there. `_step` asks for the slot of the device its fit chose, says `node.waiting` if it must
wait, and once it holds the slot fits again when it waited, since the memory has changed; a fit
that then picks another device lets go and asks for that one. The slot is held from the fit to the
attempt's end, so a retry after `oom` asks again. With 021, each item's run takes its turn, so a
click's run slips between the items of a long for each. Waiting watches the run's stop.

**The same work** (`scheduler.py`): the scheduler keeps the keys being made across its runs; a
step that finds its key there waits for that attempt, then reads the cache, and runs itself only if
that attempt failed.

**The server** (`server.py`): `_run` becomes the runs by id, each with its stop and the runtimes it
uses; `graph.run` refuses only past `MAX_RUNS` (8, in code); `run.stop` sets one stop or all;
`_refuse_if_running` and `shutdown` read every run. The list of files a process is reading
(`models/runs.py`, `reading`) is one file a process, which a second run would overwrite: it becomes
the union of what this process's runs read, written again as each starts and ends. `graph`/`busy`
in `errors.py` and architecture.md's table now reads "too many runs are going".

**The protocol** (`contracts/protocol.json`): `graph.run` and `graph.validate` take `upto` (a list
of steps); `node.waiting` (`step`, `node`, `device`, `behind`: a run and a step) is a new event;
`run.stop`'s answer lists the runs it stopped.

**The page** (`main.js`, `run-view.js`): every card has a "Run to here" button; Run stays enabled
while a run goes. The run view keeps each step's state from the newest run that named it in its
`run.start`, so an older run's late events do not overwrite a newer one's; `node.waiting` is a
state with its own words ("Waiting for the card, behind Depth"), never colour alone. Stop stops
the runs this page started.

**Docs:** architecture.md (Events: `upto`, `node.waiting`, several runs; the server's opening
lines; the failures table), nodes.md's bench section, and the handoff.

Risks: two attempts at once in one process touch shared state (the in-process executor takes the
engine's slot, so engine nodes never overlap; the cache's commit is already safe for two writers);
a card slot held by a step that hangs (Stop ends its process tree, as today); memory that a run on
the processor takes from one on the card (each fit reads system memory fresh).

## Tests

- Added: the tests the criteria name; `test_one_walk_serves_fits_upto_and_the_bench`
  (`test_graph.py`): `reaches` and `part` answer a fixed graph, and `bench_fit` defines no walk.
- Changed: tests that expect a second run to be refused `busy` (`test_server_and_nodes.py`,
  `test_runtime_server.py`) expect it to run, or to be refused at the bound; tests that import
  `upstream_of` or `part_of` from `bench_fit` use `graph.py`'s.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run engine:check` or `npm run e2e`, in CI on Windows and Ubuntu.
The tests use nodes that hold until released, never a sleep, and every wait has a deadline (spec
014). No hardware step: two real models side by side on a card and the processor is first seen in
step 4's report.

## Decisions taken

- A slot per device, not memory shared on a card: two attempts on one card would need the fit to
  count what the other will still take, which spec 004's accounting brings (`held_mb`).
- First come first served: a run cannot starve, and no priority has to be explained.
- Engine nodes take a slot of their own, one at a time: nothing in the engine's process was
  written to run beside itself, and they are short.
- `upto` plans only the part: a graph being built runs as far as it is built.
- A bound of 8 runs: each is a thread, and a page that loops must not start a thousand.

## Out of scope

- Two attempts on one card at once, and a warm worker's memory counted: spec 004.
- A run up to a node from the cache alone ("show what is there"), and a queue the person can
  reorder: the editor, step 7.
- More than one card used at once by choice: the fit picks the card today; the slot only follows.

## Open questions

1. **Run while a run goes.** Today the second is refused. Recommended: it starts, and each of its
   steps waits its turn for its device, saying what it waits behind; a step on the card therefore
   still waits for the card, and only spec 004 changes that. The other way keeps one run and
   queues whole runs, which is simpler and leaves a click waiting for a long run's end.
