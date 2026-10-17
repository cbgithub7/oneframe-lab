# 010: The scheduler and its executors, ready for spec 004

Status: draft
Owner approval: (date, once approved)

The fourth of seven clean-up specs (007 to 013), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md), the [roadmap](../../docs/roadmap.md)'s
"Ready for spec 004", "Long functions" and "One way each" items, and the
[handoff](../../docs/handoff.md)'s open item on endings. It lands after 009, whose `Job` and `Done`
its executors take and return, and before 011 and 012.

## Problem

Spec 004 keeps models loaded in a worker per runtime, and the scheduler cannot be given one: it
builds a `ProcessExecutor` inline from five per-runtime callables. `Scheduler.__init__` takes 15
parameters, `_attempt` is 123 lines with 9 arguments, and a run's key caches are reset on the shared
instance. Around it:

- each bench builds its own job and `ProcessExecutor` to run a probe;
- a processor run's `CUDA_VISIBLE_DEVICES=-1` rides in the job, though it belongs to the process;
- the type of an event sink is written eight times: six `Emit` aliases, `Say` in
  `oneframe/models/pin.py`, and the server's own;
- `runtime.stopped`, `runtime.failed` and a run's three endings are sent before their place is let
  go, so a page that acts on hearing one is refused `busy` or `in_use`: the slot is freed in a
  `finally` after the send. Model jobs already let go first, a second way.

## Requirements

1. **The scheduler is given its executors.** `Scheduler(registry, cache, *, runtimes, executors,
   store, machine, settings, learned, tmp_root, journal_dir)`. It asks `executors` for each
   attempt's executor, and `runtimes` only for what a fit and a cache key read (`device_target`,
   `key_for`). No per-runtime callable, `on_pid` or `log_dir` is left on it, nor on `for_root` or
   `bench_fit.make_scheduler`, to which only tests passed `runtime_python` and `on_pid`.
2. **Two Protocols in `oneframe/executors.py`:**
    - `Executor.execute(job: Job, emit: Emit, stop: threading.Event) -> Done`, which
      `EngineExecutor` and `ProcessExecutor` already are after 009;
    - `Executors.executor(manifest, device) -> Executor`, raising `RuntimeMissing` when the node's
      runtime cannot run here.
3. **One process per run is one provider.** `OneProcessPerRun(runtimes, *, log_dir, tmp_root,
   on_pid)` gives engine nodes the one `EngineExecutor`, and a runtime node a new `ProcessExecutor`
   per attempt, from its runtime's interpreter, variables and cache folder. On the processor it adds
   `CUDA_VISIBLE_DEVICES=-1` to that process. A job carries no environment.
4. **One probe helper.** `executors.run_probe(runtimes, runtime_id, folder, entry, *, device,
   params, log_dir)` runs a runtime's own code as a job and returns its `Done`. `bench:runtime`'s
   probe and `bench:fit`'s context probe both use it. It builds its `ProcessExecutor` as the provider
   does, and never hides the cards: the context probe runs uncapped, "on the processor", to measure
   the card.
5. **Step logs are pruned when one opens,** by the executor that opens it, as journals are.
6. **`_attempt` in named steps.** A run's state and a step's state are two small dataclasses, and
   `_attempt` builds the job, runs it, keeps the result and says `node.done`, each a method of its
   own. Events, records and messages stay as they are, byte for byte.
7. **One `Emit`,** defined in `oneframe.node` (009) and imported by every other module.
8. **An ending is sent after its place is free** (open question 1). One helper in
   `oneframe/journal.py` writes the ending to the journal, lets go of the place, then sends it:
    - an install's `runtime.stopped` and `runtime.failed`, after its slot and its runtime's lock
      (`runtime.done` already is);
    - a run's `run.done`, `run.stopped` and `run.failed`, after its list of the files it reads is
      gone and the engine's run slot is free: `Scheduler.run` takes a `release`, which the engine
      passes;
    - a model job's three endings, which already work this way, through the same helper;
    - an engine crash's ending, in each of the three.

## Acceptance criteria

- [ ] AC1: `test_the_scheduler_runs_a_node_through_the_executors_it_is_given`: with no runtime
  installed (`NoRuntimes`), a fake `Executors` receives the `Job` of a runtime node, and the outputs
  it returns reach `node.done`. Fails today: the scheduler takes no executors.
- [ ] AC2: `rg -n 'runtime_python|runtime_env|runtime_caches|runtime_key|on_pid|log_dir' engine/src/oneframe/scheduler.py engine/src/oneframe/bench_fit.py`
  prints nothing. Today it prints 37 lines.
- [ ] AC3: `test_the_provider_hides_the_cards_from_a_processor_run_only` (renamed, under Tests):
  through `OneProcessPerRun`, a runtime node on the processor sees `CUDA_VISIBLE_DEVICES=-1`, and on
  the card sees what its runtime's variables say. Fails today: there is no provider.
- [ ] AC4: `rg -l 'ProcessExecutor\(' engine/src/oneframe` prints only
  `engine/src/oneframe/executors.py`. Today it also prints `scheduler.py`, `bench_fit.py` and
  `bench_runtime.py`.
- [ ] AC5: `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does`: `run_probe` with device
  `cpu`, for a runtime whose variables name a card, runs code that sees that card. Fails today:
  there is no helper. It guards a path that otherwise runs only on hardware.
- [ ] AC6: `rg -n 'Callable\[\[dict\[str, Any\]\], None\]' engine/src/oneframe` prints one line,
  under `engine/src/oneframe/node/`. Today it prints eight, in eight files.
- [ ] AC7: `test_an_ending_is_sent_after_its_place_is_free`, over `runtime.stopped`,
  `runtime.failed`, `run.done`, `run.stopped` and `run.failed`: what the place refused while it
  lasted (the next install, the next run, removing what the run used) is accepted from a listener
  that asks on hearing the ending. Fails today in every case. Only if the owner says yes to
  question 1.
- [ ] AC8: `Scheduler._attempt` is under 60 lines, measured once from Python's `ast` and pasted in
  the PR. Today it is 123.

## Design

- **`oneframe/executors.py`** gains `Executor`, `Executors`, `OneProcessPerRun` and `run_probe`; the
  last two share one private builder of a runtime's `ProcessExecutor`. `ProcessExecutor` prunes its
  log folder (`journal.prune_step_logs`) before it opens a step's log.
- **`oneframe/runtimes.py`** gains `RuntimeSide`, a Protocol of the five methods `Runtimes` groups
  under "what the scheduler asks" (`python_for`, `env_for`, `caches_for`, `key_for`,
  `device_target`), and `NoRuntimes`, which fails every runtime node `not_installed`. It replaces
  `scheduler.no_runtimes`, `no_env`, `no_target` and two lambdas.
- **`oneframe/scheduler.py`** takes the signature above; `executors` defaults to
  `OneProcessPerRun(runtimes, tmp_root=tmp_root)`, and `tmp_root` stays for the run's reading list.
  `_executor`, `self.engine`, the caches reset on the instance and the step-log pruning go.
  `for_root` builds the provider with the root's step logs. pyright holds both executors and the
  provider to their Protocols.
- **Endings.** The helper keeps the ending from the sink, writes it to the journal, calls `release`,
  then sends it, so the journal still ends with it. `Runtimes.run_install`, `Scheduler.run` and
  `models/jobs.py` use it. `Engine.graph_run` passes a `release` that frees the slot only while it
  is still this run's, and the server's crash path frees before it says.
- **Spec 004** adds a worker pool as another `Executors`; the scheduler, `for_root`'s callers and
  the events do not change. It adds a `close()` with the workers that need one.
- **Docs that change:** `docs/architecture.md` (the Scheduler, Executors and Journal rows, and the
  Events paragraph on letting go before an ending, which now covers runs and installs),
  `docs/handoff.md` (the open item on endings goes) and `docs/roadmap.md` (the swappable executor,
  `_attempt`, `Scheduler.__init__` and the `Emit` alias go). No rule or command in AGENTS.md changes.
- **Risks:**
    - A run or a job asked for on hearing an ending may send its first events before that ending;
      they carry `run` or `job`. An install's events carry no id, so `Runtimes` lets go and sends
      under its claim lock, made re-entrant: a claim from another thread waits for the ending, and
      the listener that hears it may claim at once.
    - `release` runs on the run's thread while the next run may already be starting, so it frees
      the slot only if it is still this run's, as model jobs' `_free` does.
    - The context probe on a card runs only on hardware; AC5 holds what it sees in CI.
    - Tests that wait for `engine._run` to clear would race the ending; they wait for the ending.

## Tests

- Added:
    - `test_scheduler.py`: `test_the_scheduler_runs_a_node_through_the_executors_it_is_given`;
    - `test_process.py`: `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does`;
    - `test_runtime_server.py`: `test_an_ending_is_sent_after_its_place_is_free`, five cases;
    - `test_journal.py`: `test_a_new_step_log_prunes_the_old`.
- Renamed: `test_a_job_brings_its_own_environment` -> `test_the_provider_hides_the_cards_from_a_processor_run_only`
- Changed, names kept:
    - each test passing `runtime_python`, `runtime_env`, `target`, `runtime_key` or `on_pid` passes a
      runtimes double (a `conftest.py` fixture whose runtime is the engine's own Python) or
      `executors=`: in `conftest.py`, `test_scheduler.py`, `test_cache_keys.py`, `test_process.py`,
      `test_runtime_install.py`, `test_model_runs.py` and `test_bench_fit.py`;
    - `test_an_engine_gives_each_runtime_node_its_runtimes_cache_folder` asks the engine's provider;
    - in `test_runtime_server.py`, the three waits on `engine._run` become waits on the ending, so
      `test_a_runtime_a_run_is_using_is_neither_installed_over_nor_removed` removes the runtime as
      soon as it hears `run.stopped`.
- Removed: none.

## Verification

AC2, AC4 and AC6 are commands run in the PR, and AC8's measure (each function's
`end_lineno - lineno + 1`) is pasted there. AC1, AC3, AC5, AC7 and every test above run in CI on
ubuntu-latest and windows-latest. No step needs a GPU or a local session. `bench:fit`'s context
probe is not run on a card: AC5 holds its environment, and nothing here is claimed on a GPU.

## Decisions taken

- The provider answers for engine nodes too, so the scheduler asks one place where a node runs.
- The provider is asked per attempt, so a retry after `oom` still gets a new process.
- `CUDA_VISIBLE_DEVICES=-1` is the process's, not the job's: a spec 004 worker serves many jobs.
- `key_for` and `device_target` stay the runtimes' to answer: a worker pool changes neither.
- `run_probe` never hides the cards: the context probe runs uncapped but measures the card.
- Step logs are pruned when one opens, as journals are, so `bench:runtime`'s are bounded too.
- `on_pid` moves to the provider, not away: it names the interpreter behind a venv's launcher.
- `tmp_root` stays on the scheduler for the reading list, and the provider gets it for job folders.
- `Scheduler.run` takes `release` rather than returning its ending, so no caller has to send it.
- The ending helper lives in `journal.py`: the journal must end with the ending, written first.
- No `close()` on `Executors` until spec 004: one process per run holds nothing between runs.
- `_attempt` is measured once in the PR, not held by a lasting test; 012 asks the owner about one.
- `Emit` lives in `oneframe.node`: it is standard library only, so any engine module may import it.

## Out of scope

- The typed `Job` and `Done`, the node API and the child's split (009).
- Processes, the one stop type and children's environments (008).
- The worker pool and `Executors.close()` (spec 004).
- `RuntimeSide` moving with the manager into a package, `bench_fit` naming the `torch` runtime,
  spec 002's AC8 scenario leaving the package, and the install's and the parser's long functions
  (012).
- Requests answered on the engine's input loop, which can hold up `run.stop`: still open in the
  handoff, for first light.
- The tests' wall-clock bounds, including those in tests this spec changes (013).

## Open questions

1. **Is an ending sent only after its place is free?** Today a page that, on hearing
   `runtime.stopped`, `runtime.failed`, `run.done`, `run.stopped` or `run.failed`, starts the next
   install or run, or removes what the run used, is refused `busy` or `in_use`. After, it is
   accepted; a next run's first events may then come before the last run's ending, told apart by
   `run` as model jobs' are by `job`, and a next install's never do. *Recommended: yes.* Model jobs
   end this way already, so it makes one way, and a page should be able to act on what it hears.
