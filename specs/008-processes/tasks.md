# 008: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. `oneframe/environment.py`: move the variable lists, `child_env`, `reserved_env` and
  `reserved_why` from `executors.py`, and `uv_environment` from `runtime_install.py` (whose
  `DROP_ENV` goes), with one `ENGINE_PYTHON` and `SECRETS` list, `inherited(base)` used by both,
  and names compared upper-cased. `scratch.py` takes `TEMP_VARIABLES` from it, and `runtimes.py`
  imports it instead of the executors. Update the imports in the engine and the tests, and the
  docstrings that name `executors.FORCED_ENV`; add `test_uv_and_runs_drop_the_same_names_in_any_case`
  (AC4). AC2's command prints only `environment.py`.
- [x] 2. If the owner said yes to question 2: `reserved_env` covers `PYTHONIOENCODING` and
  `PYTHONUNBUFFERED`, `reserved_why` gives the reason, and
  `test_a_runtime_cannot_set_how_a_run_speaks_to_the_engine` (AC8); the `env` paragraph in
  `docs/runtimes.md` says so.
- [x] 3. JavaScript: `app/main/paths.js` exports the temporary variables and uv's settings,
  `scripts/tool.js` imports the temporary variables, and `contracts/layout.json` gains its
  `environment` entry, checked by `test_the_temporary_and_uv_variables_match_the_contract` and its
  twin in `tests/app/layout.test.js`.
- [x] 4. One stop type, as Design's Stop item says, and the tests' stop callables become events as
  Tests lists (the two scheduler tests that count calls set theirs from the listener). AC3's command
  prints nothing.
- [x] 5. `oneframe/processes.py`: `Child`, `run`, `output`, `alive` and `wait_gone`, with
  `engine/tests/test_processes.py` (`test_stop_during_a_step_ends_its_whole_tree`, AC7;
  `test_every_start_gets_its_own_group_and_no_window`; `test_kill_waits_for_the_processes_it_is_told_of`;
  `test_kill_after_the_end_never_raises`). `conftest.py` imports `alive` (its `process_alive`) and
  `wait_gone` from it.
- [x] 6. The install on `processes`: `run_step` hands its work to `run` (the seam
  `test_a_failed_sync_keeps_what_was_installed` patches stays), `_output` uses `output`, and
  `_kill_tree`, `NO_WINDOW` and `NEW_GROUP` go.
- [x] 7. `hardware` defaults its `run` to `processes.output` (its tests' fakes unchanged), and
  `diagnose._uv_version` uses `output`; both catch `processes.TimeoutExpired`.
- [x] 8. `ProcessExecutor` on one `Child`: the tree kill (if the owner said yes to question 1), the
  bounded wait for the interpreter the child reports, then the job folder with `files.remove_tree`,
  logged when it will not go. Add `test_stop_ends_the_processes_a_node_started` (AC5) and
  `test_ctrl_c_during_a_run_ends_its_processes`; tighten `test_stop_kills_a_runtime_child` (AC6).
- [x] 9. `bench_fit._holding` on a `Child`: UTF-8, no window, its stderr to the tool's own, ended by
  closing its stdin and then the tree kill. AC1's command prints only `processes.py`.
- [x] 10. Guards: `faulthandler_timeout = 300` in `engine/pyproject.toml`; `stop_after(seconds)`
  from a fixture in `engine/tests/conftest.py`, an `Event` a daemon timer sets, cancelled at
  teardown; and
  `test_a_node_that_queries_its_stdin_does_not_hang`, under `stop_after`.
- [x] 11. Docs: each one Design lists under "Docs that change".
- [ ] 12. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC8 one by
  one, with AC5 and AC6 from the Windows CI job.
