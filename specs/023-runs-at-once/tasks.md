# 023: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **Slots.** `oneframe/slots.py`: a slot for each card, the processor and the engine's
  process, taken first come first served, a wait that watches the run's stop; the `Scheduler` is
  given one set and `_step` takes the step's slot after the cache and before the fit, moving once
  to the slot its fit chose, and holds it to the attempt's end. `node.queued` in
  `contracts/protocol.json` (`app/protocol.d.ts`). A test node that holds until released, in
  `conftest.py`. AC2, AC3.
- [ ] 2. **The same work once.** The scheduler keeps the key of each step being made across its
  runs; a step that finds its key there says `node.queued` behind it, waits, and takes the normal
  path. AC5.
- [ ] 3. **What a process reads.** `models/runs.py`'s `reading()` lists the union of what this
  process's runs read, written under a lock as each starts and ends. AC7.
- [ ] 4. **The server.** The runs by id, each with its stop and the runtimes it uses; `MAX_RUNS`;
  `run.stop` stops one run or every run, and takes queued runs off only when it names none, its
  answer listing the runs it stopped; the guards and `shutdown` read every run; `graph`/`busy`
  reads "too many runs are going". AC1, AC4, AC6, `test_runs_beyond_the_bound_are_refused`,
  `test_a_stop_that_names_a_run_leaves_queued_runs`, `test_a_stop_cancels_a_queued_run` changed.
- [ ] 5. **The page.** Run and Run to here stay enabled while a run goes; the run view follows
  every run this page started, each card from the newest run that named it; `node.queued` is the
  phase `queued`, in words, behind what; Stop stops every run. "a newer run's state is not
  overwritten by an older run's events" (`tests/app/page.test.js`).
- [ ] 6. **The e2e test.** A fixture graph with two cards on the engine's slot, and "a second run
  waits for the device and says so". AC8.
- [ ] 7. **Docs and the end.** architecture.md (Events: `node.queued`, several runs; the server;
  the failures table), the handoff, the local step under "Waiting on a local session"; the full
  checks; `/code-review`; the spec marked done in the PR.
