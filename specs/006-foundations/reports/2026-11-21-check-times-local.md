# The checks' times on the owner's PC, with the engine's fast tier

2026-11-20 19:24 (UTC−5). Commit `98635db` (`fix/review-2026-11-07-decisions`, PR #72), on the
owner's PC: Windows, an Intel Core i7-4770K at 3.50 GHz, Node 24.22.1, uv 0.13.5. Its card, a
GTX 1070, plays no part in these checks. From the handoff's "Waiting on a local session" item; the
review of 2026-11-07, decision 5.

There is no pass or fail. The item asked for each check's time, so that the next change to the
fast tier rests on numbers. Every check ended with exit code 0.

## Times

| Check | Seconds | pytest's own | Tests |
| --- | --- | --- | --- |
| `npm run check` | 45.6 | | 69 node tests passed |
| `npm run engine:check` (full) | 276.5 | 239.6 | 676 passed, 4 skipped |
| `npm run engine:check:fast` | 128.5 | 106.3 | 642 passed, 3 skipped, 35 deselected |
| `node scripts/test-guard.js` | 3.9 | | 749 at `origin/main`, 751 now |
| `pytest engine -m "not real_uv" --durations=30`, alone | 109.0 | 107.6 | as the fast tier |

The skips are the four Windows skips the engine's tests already had (the review of 2026-11-07
counted 4); the fast tier leaves out one of them with the `real_uv` tests.

## What they say

- **The fast tier halves the engine's check here:** 128.5 s against 276.5 s in the same session,
  148 s (54 %) saved. Of that, 133 s is pytest's own: the 35 `real_uv` tests take about 3.8 s each.
- **A turn that changed something now costs about 3 minutes, not 5 and a half:** the stop hook
  runs `npm run check`, the fast tier and the test guard, 178 s in all, against 326 s with the
  full engine check. The review of 2026-11-07 measured 440 s (32 + 396 + 12) for the old hook on
  this PC, on another day and commit.
- **Outside pytest,** ruff, pyright, `runtime:check` and the starts of npm and uv take about 22 s
  in the fast tier and 37 s in the full one. Neither tier changes them.
- **The fast tier is twice the cloud's,** 106 s of pytest against 47 s there (2026-11-12) for the
  same 645 tests. The owner's note: the PC is about ten years old, and so is its Windows install,
  against a fresh system on new hardware in the cloud. It is spread across the tests, not in a few: the 30 slowest take 35.3 s, and the
  other 615 take 71 s, about 0.12 s each.
- **Two tests are about ten times slower here than in the cloud,** and lead the list:
  `test_ac4_the_store_keeps_its_bound` (3.77 s, 0.38 s in the cloud) and
  `test_the_model_nodes_fit_a_range_of_machines` (3.33 s, under 0.34 s there). The first writes
  `learned.json` once per entry, each a locked write and a move into place; the second writes
  nothing and only fits every model node to every test machine, twice. Why either is this much
  slower here is not profiled.

## The 30 slowest tests in the fast tier

| Seconds | Test |
| --- | --- |
| 3.77 | `test_memory.py::test_ac4_the_store_keeps_its_bound` |
| 3.33 | `test_model_nodes.py::test_the_model_nodes_fit_a_range_of_machines` |
| 2.49 | `test_processes.py::test_nothing_is_handed_on_after_a_kill` |
| 1.91 | `test_server_and_nodes.py::test_an_engine_node_that_imports_numpy_runs_through_the_server` |
| 1.46 | `test_model_pin.py::test_a_tool_claims_again_until_the_store_is_let_go_saying_so_once` |
| 1.43 | `test_processes.py::test_stop_during_a_step_ends_its_whole_tree` |
| 1.34 | `test_bench_fit.py::test_the_bench_learns_nothing_so_every_estimate_is_the_nodes_own` |
| 1.33 | `test_bench_fit.py::test_bench_fit_measures_a_node_in_a_graph` |
| 1.26 | `test_bench_fit.py::test_a_node_is_measured_at_each_group_of_settings` |
| 1.26 | `test_scheduler.py::test_nvidia_smi_is_read_only_for_a_runtime_node_that_can_use_a_card` |
| 1.14 | `test_model_transport.py::test_each_fault_heals_without_a_second_request_from_the_person[429 RateLimit]` |
| 1.07 | `test_model_transport.py::test_each_fault_heals_without_a_second_request_from_the_person[429 Retry-After]` |
| 1.05 | `test_hosts.py::test_a_stall_holds_the_connection_until_released` |
| 0.93 | `test_server_and_nodes.py::test_the_server_answers_and_runs_a_graph` |
| 0.91 | `test_model_runs.py::test_a_result_is_stored_under_the_manifest_the_run_used` |
| 0.89 | `test_listing.py::test_a_case_numbered_by_its_position_is_refused` |
| 0.82 | `test_server_and_nodes.py::test_shutdown_stops_a_running_graph_and_its_node_process` |
| 0.82 | `test_scheduler.py::test_a_second_oom_fails_with_both_fits_and_both_peaks_and_caches_nothing` |
| 0.81 | `test_cache_keys.py::test_a_runtime_node_runs_again_when_its_runtime_changes` |
| 0.72 | `test_scheduler.py::test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card` |
| 0.72 | `test_scheduler.py::test_a_runtime_node_that_runs_out_is_retried_in_a_new_process` |
| 0.69 | `test_model_transport.py::test_each_fault_heals_without_a_second_request_from_the_person[stall mid-body]` |
| 0.69 | `test_journal.py::test_diagnose_removes_the_user_from_every_spelling_and_scrubs_tokens` |
| 0.68 | `test_rules.py::test_no_engine_module_names_a_runtime_a_node_or_the_tests` |
| 0.64 | `test_model_transport.py::test_each_fault_heals_without_a_second_request_from_the_person[stall before the headers]` |
| 0.64 | `test_model_jobs.py::test_a_killed_engine_leaves_a_partial_the_next_download_resumes` |
| 0.63 | `test_model_pin.py::test_a_store_held_past_the_wait_is_refused_locked_as_before` |
| 0.63 | `test_journal.py::test_npm_run_diagnose_writes_one_file_to_reports` |
| 0.61 | `test_process.py::test_stop_ends_the_processes_a_node_started` |
| 0.59 | `test_server_and_nodes.py::test_a_graph_round_trips_over_the_protocol` |

All are under `engine/tests/`, and all are calls, not set-up.

## How it ran

- Grok Bot started a PowerShell 7 window and ran one script there, which checked that the checkout
  had no local changes, switched to the PR's branch, ran the five commands in order with a stopwatch
  around each, wrote every line to `check-times.log` (ignored by git), and switched back.
- **Outside Claude desktop's package:** the log's process chain reads `pwsh.exe < pwsh.exe` and
  ends there, because the process that started the window had already exited. It names no
  `claude.exe`. The checkout is `D:\GitHub_Repositories\Applications\oneframe-lab`, which is not
  under `\Packages\`. The full check's footprint test, which watches the real profile, passed.
- The log shows node's test marks as `Γ£ö`: the script read the commands' output in the console's
  code page, not UTF-8. A later script sets `[Console]::OutputEncoding` to UTF-8 first. No figure
  is affected.
