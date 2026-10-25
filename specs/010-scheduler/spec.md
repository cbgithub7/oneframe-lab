# 010: The scheduler and its executors, ready for spec 004

Status: done (merged 2026-10-25 in [PR #39](https://github.com/cbgithub7/oneframe-lab/pull/39); task 13, the probes on a card, passed on a GTX 1070 on 2026-10-25: [report](reports/2026-10-25-local.md))
Owner approval: 2026-10-18, every open question answered as recommended
Changed after approval (from `/code-review`, 2026-10-18), approved by the owner 2026-10-23:
  `NoRuntimes` raises only where a runtime is needed, so AC1's fake executors still get the job.

The fourth of eight clean-up specs (007 to 014), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md), the [roadmap](../../docs/roadmap.md)'s
"Ready for spec 004", "Long functions" and "One way each" items, and the
[handoff](../../docs/handoff.md)'s open item on endings. It lands after 009, whose `Job` and `Done`
its executors take and return, and before 011 and 012.

## Problem

Spec 004 keeps models loaded in a worker per runtime, and the scheduler cannot be given one: it
holds five per-runtime callables and builds a `ProcessExecutor` from three of them for each
attempt. `Scheduler.__init__` takes 15 parameters, `_attempt` is 123 lines with 9 arguments, and a
run's key caches are reset on the shared instance. Around it:

- each bench builds its own job and `ProcessExecutor` to run a probe;
- the scheduler, not what starts the process, hides the cards from a processor run;
- `on_pid` runs through the scheduler, both its builders and the executor, and only tests pass it;
- the type of an event sink is written eight times: six `Emit` aliases, `Say` in
  `oneframe/models/pin.py`, and the server's own;
- `runtime.stopped`, `runtime.failed` and a run's three endings are sent before their place is let
  go, so a page acting on hearing one is refused `busy` or `in_use` (open question 1). Model jobs
  already let go first, a second way.

## Requirements

1. **The scheduler is given its executors.** `Scheduler(registry, cache, *, runtimes, executors,
   store, machine, settings, learned, tmp_root, journal_dir)` asks `executors` for each attempt's
   executor, and `runtimes` only for a fit's `device_target` and a cache key's `key_for`. No
   per-runtime callable or `log_dir` is left on it, nor on `for_root` or `bench_fit.make_scheduler`.
2. **Two Protocols in `oneframe/executors.py`:**
    - `Executor.execute(job: Job, emit: Emit, stop: threading.Event) -> Done`, which
      `EngineExecutor` and `ProcessExecutor` already are after 009;
    - `Executors.executor(manifest, device) -> Executor`, raising `RuntimeMissing` when the node's
      runtime cannot run here.
3. **One process per run is one provider.** `OneProcessPerRun(runtimes, *, tmp_root, log_dir=None)`
   gives engine nodes the one `EngineExecutor`, and a runtime node a new `ProcessExecutor` per
   attempt, from its runtime's interpreter, variables and cache folder. On the processor it adds
   `CUDA_VISIBLE_DEVICES=-1` to that process, and the scheduler no longer does.
4. **One probe helper.** `executors.run_probe(runtimes, runtime_id, folder, entry, *, device,
   params, log_dir)` runs a runtime's own code as a job and returns its `Done`, never hiding the
   cards. `bench:runtime`'s probe and `bench:fit`'s context probe both use it.
5. **Step logs are pruned when one opens,** by the executor that opens it.
6. **`_attempt` in named steps.** A run's state and a step's state are two small dataclasses, and
   `_attempt` builds the job, runs it, keeps the result and says `node.done`, each a method of its
   own. Events, records and messages stay as they are, byte for byte.
7. **One `Emit`,** defined in `oneframe.node` (009) and imported by every other module.
8. **No `on_pid`.** It leaves the scheduler, `for_root`, `make_scheduler` and `ProcessExecutor`, and
   no provider takes it.
9. **An ending is sent after its place is free** (open question 1). One helper in
   `oneframe/journal.py` writes the ending to the journal, lets go of the place, then sends it:
    - an install's `runtime.stopped` and `runtime.failed`, after its slot and its runtime's lock
      (`runtime.done` already is);
    - a run's three endings, after its list of the files it reads is gone and the engine's run slot
      is free, through a `release` the engine passes to `Scheduler.run`;
    - a model job's three endings, which already work this way, through the same helper;
    - an engine crash's ending, in each of the three.

## Acceptance criteria

- [ ] AC1: `test_the_scheduler_runs_a_node_through_the_executors_it_is_given`: with no runtime
  installed (`NoRuntimes`), a fake `Executors` receives the `Job` of a runtime node, and the outputs
  it returns reach `node.done`. Fails today: the scheduler takes no executors.
- [ ] AC2: `rg -n 'runtime_python|runtime_env|runtime_caches|runtime_key|on_pid|self\.log_dir|prune_step_logs' engine/src/oneframe/scheduler.py engine/src/oneframe/bench_fit.py`
  prints nothing. Today it prints 36 lines.
- [ ] AC3: `test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card` (009's name), through
  `OneProcessPerRun`: a runtime node on the processor sees `CUDA_VISIBLE_DEVICES=-1`, and on the
  card sees what its runtime's variables say. Fails today: there is no provider.
- [ ] AC4: `rg -l 'ProcessExecutor\(' engine/src/oneframe` prints only
  `engine/src/oneframe/executors.py`. Today it prints `scheduler.py`, `bench_fit.py` and
  `bench_runtime.py`, and not `executors.py`.
- [ ] AC5: `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does`: `run_probe` with device
  `cpu`, for a runtime whose variables name a card, runs code that sees that card. Fails today:
  there is no helper. It guards in CI a path that otherwise runs only on hardware.
- [ ] AC6: `rg -n '^(Emit|Say) = |Callable\[\[dict\[str, Any\]\], None\]' engine/src/oneframe`
  prints one line, under `engine/src/oneframe/node/`. Today it prints eight, in eight files.
- [ ] AC7: `test_an_ending_is_sent_after_its_place_is_free`, over `runtime.stopped`,
  `runtime.failed`, `run.done`, `run.stopped` and `run.failed`: a listener that, on hearing the
  ending, asks for what its place refused while it lasted (the same install; a next run, or
  removing the run's runtime or a file it read) is answered, not refused `busy` or `in_use`. Fails
  today in every case. Only if the owner says yes to question 1.
- [ ] AC8: `Scheduler._attempt` is under 60 lines (`end_lineno - lineno + 1` from Python's `ast`),
  measured once and pasted in the PR. Today it is 123.

## Design

- **`oneframe/executors.py`** gains `Executor`, `Executors`, `OneProcessPerRun` and `run_probe`; the
  last two share one private builder of a runtime's `ProcessExecutor`. `ProcessExecutor` prunes its
  log folder (`journal.prune_step_logs`) before it opens a step's log; 008's Stop still uses the
  interpreter's id the child reports.
- **`oneframe/runtimes.py`** gains `RuntimeSide`, a Protocol of the five methods `Runtimes` groups
  under "what the scheduler asks" (`python_for`, `env_for`, `caches_for`, `key_for`,
  `device_target`), and `NoRuntimes`: its `python_for`, `env_for` and `caches_for` raise
  `RuntimeMissing`, so a runtime node fails `not_installed`, while `key_for` and `device_target`
  answer as today's defaults do, so a fake `Executors` still gets the job (AC1). It replaces
  `scheduler.no_runtimes`, `no_env`, `no_target` and two lambdas.
- **`oneframe/scheduler.py`** makes `OneProcessPerRun(runtimes, tmp_root=tmp_root)` the default
  `executors`. `_executor`, `self.engine`, the caches reset on the instance and the step-log pruning
  go. `for_root` builds the provider with the root's step logs. `for_root` and `make_scheduler` take
  any `RuntimeSide`, so a test passes its double where it passed `runtime_python`. pyright holds
  both executors and the provider to their Protocols.
- **Endings.** The helper keeps the ending from the sink until it is in the journal and `release`
  has run, so the journal still ends with it. `Runtimes.run_install`, `Scheduler.run` and
  `models/jobs.py` end through it, and `Engine.graph_run` passes the `release`; the server's crash
  path frees before it says.
- **Docs that change:** `docs/architecture.md` (the Scheduler, Executors and Journal rows, and the
  Events paragraph on letting go before an ending, now covering runs and installs),
  `docs/handoff.md` (its open item on endings goes) and `docs/roadmap.md` (the swappable executor,
  `_attempt`, `Scheduler.__init__` and the `Emit` alias go). AGENTS.md's rules and commands stay.
- **Risks:**
    - A run or job asked for on hearing an ending may send its first events before that ending;
      they carry `run` or `job`. An install's events carry no id, so `Runtimes` lets go and sends
      under its claim lock, made re-entrant: a claim from another thread waits for the ending, and
      the listener that hears it may claim at once.
    - `release` runs on the run's thread while the next run may already be starting, so it frees
      the slot only if it is still this run's, as model jobs' `_free` does.
    - Tests that wait for `engine._run` to clear would race the ending; they wait for the ending.

## Tests

- Added:
    - `test_scheduler.py`: `test_the_scheduler_runs_a_node_through_the_executors_it_is_given`;
    - `test_process.py`: `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does`;
    - `test_runtime_server.py`: `test_an_ending_is_sent_after_its_place_is_free`, five cases;
    - `test_journal.py`: `test_a_new_step_log_prunes_the_old`, and from `/code-review`
      `test_an_ending_the_journal_cannot_take_still_lets_go_and_is_sent`.
- Renamed: none.
- Changed, names kept:
    - each test passing `runtime_python`, `runtime_env`, `target` or `runtime_key` passes a runtimes
      double (a `conftest.py` fixture whose runtime is the engine's own Python) or `executors=`: in
      `conftest.py`, `test_scheduler.py`, `test_cache_keys.py`, `test_process.py`,
      `test_runtime_install.py`, `test_model_runs.py` and `test_bench_fit.py`;
    - the two tests that counted processes with `on_pid`, the `oom` retry in `test_scheduler.py`
      and the files check in `test_model_runs.py`, read the process id their node reports;
    - `test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card` runs through the provider;
    - `test_an_engine_gives_each_runtime_node_its_runtimes_cache_folder` asks the engine's provider;
    - in `test_runtime_server.py`, the three waits on `engine._run` become waits on the ending, so
      `test_a_runtime_a_run_is_using_is_neither_installed_over_nor_removed` removes the runtime as
      soon as it hears `run.stopped`.
- Removed: `test_a_listener_is_told_the_childs_process_id` (`test_process.py`): it tests `on_pid`,
  which goes; the Stop test still checks that the process the node reports is gone.

## Verification

AC2, AC4 and AC6 are commands run in the PR, where AC8's measure is pasted. AC1, AC3, AC5, AC7 and
every test above run in CI on ubuntu-latest and windows-latest.

After the merge, a local session (`/local-session`) on a machine with an NVIDIA card runs the
benches' probes on a card once, for 009's changes to them and this spec's, as 009 defers to here:
`npm run bench:runtime -- torch` shows the probe's result, and `npm run bench:fit` (spec 002's
check) records the CUDA context's size. The report, naming the machine, goes to
`specs/010-scheduler/reports/<date>-local.md`. Nothing else here is claimed on a GPU.

## Decisions taken

- The provider answers for engine nodes too, so the scheduler asks one place where a node runs.
- The provider is asked per attempt, so a retry after `oom` still gets a new process.
- The provider, which starts the process, hides the cards: a spec 004 worker serves many jobs.
- `key_for` and `device_target` stay the runtimes' to answer: a worker pool changes neither.
- `run_probe` hides no card: the context probe runs uncapped "on the processor" to measure the card.
- Step logs are pruned when one opens, as journals are, so `bench:runtime`'s are bounded too.
- `on_pid` goes, not to the provider: only tests used it, and a node can report its own process id.
- `tmp_root` stays on the scheduler for the reading list, and the provider gets it for job folders.
- `Scheduler.run` takes `release` rather than returning its ending, so no caller has to send it.
- The ending helper lives in `journal.py`: the journal must end with the ending, written first.
- No `close()` on `Executors` until spec 004: one process per run holds nothing between runs.
- `_attempt` is measured once in the PR, not held by a lasting test; 012 asks the owner about one.
- `Emit` lives in `oneframe.node`: it is standard library only, so any engine module may import it.
- `make_scheduler` takes the data root first, as `for_root` does: a `RuntimeSide` has no root.
- `caches_for` and `key_for` may answer None (no cache folder, no runtime part), as the defaults did.
- The helper says a crash's ending too, so the server's run crash path frees and logs, saying nothing twice.
- `models:pin` keeps its own ending: a tool's reaches only its terminal, and its process ends next.

## Out of scope

- The typed `Job` and `Done`, the node API and the child's split (009).
- Processes, the one stop type and children's environments (008).
- The test guard (007); files, hashing, downloads and paths taken from the layout (011).
- The worker pool, another `Executors` with a `close()` the engine's shutdown calls; the scheduler
  and the events do not change for it (spec 004).
- `RuntimeSide` moving with the manager into a package, and the install's and the parser's long
  functions (012); `bench_fit` naming the `torch` runtime, and spec 002's AC8 scenario leaving the
  package (013).
- Requests answered on the engine's input loop, which can hold up `run.stop`: still open in the
  handoff, for first light.
- The tests' wall-clock bounds, including those in tests this spec changes (014).

## Open questions (answered by the owner, 2026-10-18)

1. **Is an ending sent only after its place is free?** Today a page that, on hearing
   `runtime.stopped`, `runtime.failed`, `run.done`, `run.stopped` or `run.failed`, starts the next
   install or run, or removes what the run used, is refused `busy` or `in_use`. After, it is
   accepted; a next run's first events may then come before the last run's ending, told apart by
   `run` as model jobs' are by `job`, and a next install's never do. *Answered: yes.* Model jobs
   end this way already, so it makes one way, and a page should be able to act on what it hears.
