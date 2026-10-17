# 009: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [ ] 1. **The child's parts move to `oneframe/node/`, behaviour unchanged.** Create the package:
  `failures.py`, `usage.py`, `context.py` (today's `NodeContext`), `code.py` (today's
  `load_entry`), `process.py` (the guards), `runner.py` (`run_job` on today's dict job), and
  `__init__` (`Value`, `Stopped`, `NodeFailed`, `Emit`). Add the 3.11 entries for it to `ruff.toml`
  and `pyrightconfig.json`, the latter before `engine/src`. `child.py` becomes the bootstrap: its
  `main()` registers the stub `oneframe` and `oneframe.node`, then runs the guards and the job.
  `errors.py`, `cache.py`, `scheduler.py` and `executors.py` import from `oneframe.node`, and
  `executors.CHILD` is a path beside it. Switch the tests' imports; extend
  `test_a_runtime_child_cannot_import_the_engines_own_modules` (AC2) and add
  `test_an_engine_run_leaves_its_sockets_alone`. AC1's command prints nothing. The Executors row
  of `docs/architecture.md` names `oneframe/node/`.
- [ ] 2. **A typed job.** `job.py` with `Job` and `Done`, `to_json` keeping `default=str`. The
  scheduler builds its job in one `_job` method and reads the `Done`; each bench builds a `Job`;
  both executors take a `Job` and return a `Done`, and `ProcessExecutor` writes the job and reads
  the `done` event through them. `child_command(python, job, exit_with_parent=False)` and the
  child's `--exit-with-parent`; `allow_network` is deleted; the job's `env` moves to the
  scheduler's `_executor(manifest, device)`. Tests build `Job`s; the two exit-with-parent tests use
  the argument; rename `test_a_job_brings_its_own_environment` as the spec lists it; add
  `test_a_job_survives_its_json`. AC7's command prints nothing.
- [ ] 3. **Node code as a package.** `code.py` imports the node's folder as
  `oneframe_nodes.<id>_<8 hex>` and its entry as a module in it, forgets the package's modules by
  name before a rerun, and never touches `sys.path`. The test nodes in `test_child_env.py`,
  `test_footprint.py` and `test_cache_keys.py` import their helper relatively. Add
  `test_two_engine_nodes_with_same_named_helpers_each_run_their_own` (AC3) and
  `test_a_runtime_nodes_helper_never_hides_a_package_of_its_runtime` (AC4). `docs/nodes.md` says
  helpers are imported relatively.
- [ ] 4. **The Protocols.** `Context` and `LoadContext` in `oneframe/node/__init__.py`. The
  context's `job`, `node`, `out_dir`, `outputs`, `ways` and `peaks()` become private and
  `stopped()` goes; the runner gets the `Done` from it, and the fallbacks tests read their `ways`
  and peaks there. Add `test_a_context_shows_exactly_its_protocol` with its `run` case (AC6).
  Annotate `ctx: Context` in `nodes/`, `runtimes/torch/probe.py`, `engine/tests/hardware` and the
  tiny runtime's probe, and add `runtimes` to pyright's include. AC8's command prints nothing.
  `docs/nodes.md` types `ctx` and says node code may import `oneframe.node`, and nothing else of
  the engine.
- [ ] 5. **`load`, then `run`.** `run.load` in the manifest, parsed with `run.entry`'s refusals
  (`test_run_load_is_refused_as_run_entry_is`); the scheduler puts it in the `Job`; `code.py`
  returns it; the runner calls it once with the view, then `run(ctx, state)`. Add
  `test_load_runs_once_and_run_gets_its_state` (AC5) and the `load` case of
  `test_a_context_shows_exactly_its_protocol` (AC6). `docs/nodes.md` shows `run.load` in
  `node.json` and the two functions in the code section.
- [ ] 6. **Close.** Delete from `docs/roadmap.md` the node contract, `child.py`'s five concerns, "a
  job's dict", `allow_network`, and the typed job and the child's split from "Ready for spec 004".
  Run AC1, AC7 and AC8's commands and `node scripts/check-docs.js`; the PR checks each criterion by
  name.
