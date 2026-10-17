# 010: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [ ] 1. One `Emit`: `journal.py`, `scheduler.py`, `executors.py`, `runtimes.py`,
  `runtime_install.py`, `server.py` and `models/pin.py` import it from `oneframe.node`, and so do
  `models/jobs.py` and `models/tools.py`, which take it from the journal today; `Say` goes. AC6's
  command prints one line.
- [ ] 2. `RuntimeSide` and `NoRuntimes` in `oneframe/runtimes.py`, and a `conftest.py` fixture for a
  runtimes double whose runtime is the engine's own Python, with a target and a key a test can set.
- [ ] 3. `Executor`, `Executors` and `OneProcessPerRun` in `oneframe/executors.py`, with the private
  builder of a runtime's `ProcessExecutor` and `CUDA_VISIBLE_DEVICES=-1` for a processor run. Rename
  `test_a_job_brings_its_own_environment` to
  `test_the_provider_hides_the_cards_from_a_processor_run_only`, run through the provider (AC3).
- [ ] 4. The scheduler on the provider: the keyword-only signature, `runtimes` for the fit's target
  and the cache key, `for_root` and `make_scheduler` without `runtime_python` and `on_pid`, the
  processor's `CUDA_VISIBLE_DEVICES` out of the job, and `server.py`. The tests' seams move to the
  fixture or to `executors=`, names kept. Add
  `test_the_scheduler_runs_a_node_through_the_executors_it_is_given` (AC1). AC2's command prints
  nothing.
- [ ] 5. `ProcessExecutor` prunes the step logs when it opens one, and the scheduler no longer does;
  add `test_a_new_step_log_prunes_the_old`.
- [ ] 6. `run_probe`, used by `bench_runtime._probe` and `bench_fit._probe_context`; add
  `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does` (AC5). AC4's command prints only
  `executors.py`.
- [ ] 7. A run's and a step's state as two dataclasses, the per-run caches off the instance, and
  `_attempt` in named steps, every event, record and message unchanged (the scheduler's tests hold
  them). Measure AC8 for the PR.
- [ ] 8. If the owner said yes to question 1: the ending helper in `journal.py`, and model jobs
  ending through it (`test_model_jobs.py` unchanged).
- [ ] 9. Then installs and runs end through it: `Runtimes`' claim lock made re-entrant, with the
  release and the send under it; `Scheduler.run`'s `release`; the server's slot and crash path. Add
  `test_an_ending_is_sent_after_its_place_is_free` (AC7), and turn the waits on `engine._run` in
  `test_runtime_server.py` into waits on the ending.
- [ ] 10. Docs: `docs/architecture.md`'s Scheduler, Executors and Journal rows and its Events
  paragraph on endings; the open item on endings out of `docs/handoff.md` (only with step 9); and
  from `docs/roadmap.md` what this spec takes on.
- [ ] 11. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC8 one by
  one, with AC8's measure pasted.
