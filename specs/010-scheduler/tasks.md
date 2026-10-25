# 010: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. One `Emit`: `journal.py`, `scheduler.py`, `executors.py`, `runtimes.py`,
  `runtime_install.py`, `server.py` and `models/pin.py` import it from `oneframe.node`, and so do
  `models/jobs.py` and `models/tools.py`, which take it from the journal today; `Say` goes. AC6's
  command prints one line.
- [x] 2. `RuntimeSide` and `NoRuntimes` in `oneframe/runtimes.py`, and a `conftest.py` fixture for a
  runtimes double whose runtime is the engine's own Python, with a target and a key a test can set.
- [x] 3. `on_pid` goes from the scheduler, `for_root`, `make_scheduler` and `ProcessExecutor`. The
  `oom` retry test in `test_scheduler.py` and the files-check test in `test_model_runs.py` read the
  process id their node reports; `test_a_listener_is_told_the_childs_process_id` goes, as the spec
  lists.
- [x] 4. `Executor`, `Executors` and `OneProcessPerRun` in `oneframe/executors.py`, with the private
  builder of a runtime's `ProcessExecutor` and `CUDA_VISIBLE_DEVICES=-1` for a processor run.
  `test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card` (009's) runs through the
  provider (AC3).
- [x] 5. The scheduler on the provider: the keyword-only signature, `runtimes` for the fit's target
  and the cache key, `for_root` and `make_scheduler` taking a `RuntimeSide` without
  `runtime_python`, the processor's `CUDA_VISIBLE_DEVICES` out of the scheduler, and `server.py`.
  The tests' seams move to the fixture or to `executors=`, names kept. Add
  `test_the_scheduler_runs_a_node_through_the_executors_it_is_given` (AC1).
- [x] 6. `ProcessExecutor` prunes the step logs when it opens one, and the scheduler no longer does;
  add `test_a_new_step_log_prunes_the_old`. AC2's command prints nothing.
- [x] 7. `run_probe`, used by `bench_runtime._probe` and `bench_fit._probe_context`; add
  `test_a_probe_on_the_processor_sees_the_cards_its_runtime_does` (AC5). AC4's command prints only
  `executors.py`.
- [x] 8. A run's and a step's state as two dataclasses, the per-run caches off the instance, and
  `_attempt` in named steps, every event, record and message unchanged (the scheduler's tests hold
  them). Measure AC8 for the PR.
- [ ] 9. If the owner said yes to question 1: the ending helper in `journal.py`, and model jobs
  ending through it (`test_model_jobs.py` unchanged).
- [ ] 10. Then installs and runs end through it: `Runtimes`' claim lock made re-entrant, with the
  release and the send under it; `Scheduler.run`'s `release`; the server's slot and crash path. Add
  `test_an_ending_is_sent_after_its_place_is_free` (AC7), and turn the waits on `engine._run` in
  `test_runtime_server.py` into waits on the ending.
- [ ] 11. Docs: `docs/architecture.md`'s Scheduler, Executors and Journal rows and its Events
  paragraph on endings; the open item on endings out of `docs/handoff.md` (only with task 10); and
  from `docs/roadmap.md` what this spec takes on.
- [ ] 12. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC8 one
  by one, with AC8's measure pasted.
- [ ] 13. After the merge, the local session in Verification on a machine with an NVIDIA card, and
  its report under `specs/010-scheduler/reports/`.
