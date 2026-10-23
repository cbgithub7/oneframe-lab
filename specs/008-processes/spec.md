# 008: Processes, children's environments and Stop

Status: approved
Owner approval: 2026-10-18, every open question answered as recommended
Changed after approval (from `/code-review`, 2026-10-18), approved by the owner 2026-10-23: the one
  list of the engine's Python keeps `PYTHONDONTWRITEBYTECODE` from uv, as `DROP_ENV` does today;
  `stop_after` is a fixture whose timer cannot outlive its test.

The second of eight clean-up specs (007 to 014), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
"One way each" and Windows Stop items. It lands after 007 (the test guard) and before 009, so the
node API is written once, around the one stop type.

## Problem

Processes are started, read and ended in three ways, and each is wrong somewhere:

- Stop kills a node run's first process only. A process the node started keeps running (checked on
  Linux), and on Windows the interpreter behind a venv's launcher may outlive its job folder.
- An install step ignores Stop once its output has closed, and is waited for to its end (checked).
- The bench's holder is read in the locale's encoding, may open a console window, and is killed
  without being waited for. `NO_WINDOW` is defined three times, and three short commands
  (nvidia-smi, `uv --version`, the install's own) each start differently.

Two lists, differing in members and in case, decide what a child inherits: uv still gets `HF_TOKEN`,
`CONDA_DEFAULT_ENV` and other-case spellings of the engine's variables, and runs get `UV_PYTHON`
(checked). A stop is a callable in six modules and a `threading.Event` in the rest. The roadmap's
"Stop hang on Windows" was two bugs, both fixed (commits 576e824 and e151633); no failure since
points to a third.

## Requirements

1. **One module starts, reads and ends processes.** `oneframe/processes.py` is the only engine
   module that imports `subprocess`. Every process it starts gets its own session (POSIX) or
   process group (Windows) and no console window, and is read as UTF-8, undecodable bytes replaced.
2. **Ending a process ends its tree, and waits for it.** A Stop of a node run ends every process
   the node started (open question 1); on Windows it returns only once the interpreter the child
   reported is gone, and the job folder is removed after that. A Stop during an install step ends
   the step's tree, whether or not its output has closed.
3. **One stop type.** A stop is a `threading.Event` in a parameter named `stop` wherever a stop flag
   or callable is passed today: the node context, the executors, the scheduler, the runtime install,
   the archive download and the model store's hashing. Nothing takes a callable that asks "stop?".
4. **One module names what a child inherits.** `oneframe/environment.py` holds every variable the
   engine removes from, or sets in, a child's environment, names compared upper-cased on every
   system. uv and runs start from one inherited environment, without the engine's own Python, its
   folders on `PATH`, or the secrets.
5. **A runtime's definition cannot set what every run sets.** `reserved_env` covers every name
   `child_env` sets after the definition's own, so `PYTHONIOENCODING` and `PYTHONUNBUFFERED` are
   refused as the cache variables are (open question 2).
6. **One copy per language.** `app/main/paths.js` holds the temporary-folder variables, which
   `scripts/tool.js` imports, and uv's settings, once; a new `environment` entry in
   `contracts/layout.json` holds them equal to `environment.py`'s, as it does the paths.
7. **A hang names itself.** A test still running long after the slowest test ends prints every
   thread's stack, and a node that asks about its stdin runs under a stop deadline, so a return of
   October's hang fails instead of hanging.

## Acceptance criteria

- [ ] AC1: `rg -l 'import subprocess|from subprocess|killpg|taskkill|CREATE_NO_WINDOW|os\.kill\(' engine/src/oneframe`
  prints only `engine/src/oneframe/processes.py`. Today it prints five files.
- [ ] AC2: `rg -l '"(TMPDIR|VIRTUAL_ENV|PYTHONPYCACHEPREFIX|UV_PROJECT_ENVIRONMENT|UV_CACHE_DIR|HF_TOKEN|HF_HUB_OFFLINE|HF_HOME|PYTHONDONTWRITEBYTECODE|PYTHONIOENCODING)"' engine/src/oneframe`
  prints only `engine/src/oneframe/environment.py`. Today it prints three files.
- [ ] AC3: `rg -n 'Callable\[\[\], bool\]|ShouldStop|should_stop' engine/src/oneframe` prints
  nothing. Today it prints 40 lines.
- [ ] AC4: `test_uv_and_runs_drop_the_same_names_in_any_case`: given `CONDA_DEFAULT_ENV`, `HF_TOKEN`,
  `virtual_env`, `PythonPath` and `UV_PYTHON`, neither `uv_environment` nor `child_env` keeps any.
  Fails today: uv keeps the first four, and runs keep the last.
- [ ] AC5: `test_stop_ends_the_processes_a_node_started` (CI, Ubuntu and Windows): a node starts a
  process that sleeps far longer than the test and hangs; after the stopped run returns, the sleeper
  is gone (`wait_gone`). Fails today on Linux. Only if the owner says yes to question 1.
- [ ] AC6: `test_stop_kills_a_runtime_child` asserts that the interpreter the child reported is gone
  the moment the stopped run returns, with no polling in the test, and passes on Windows CI. Today
  it polls; without the polling it may still pass by timing.
- [ ] AC7: `test_stop_during_a_step_ends_its_whole_tree` (`test_processes.py`): a step starts a
  grandchild, closes its output and sleeps; Stop makes the step raise `Stopped`, and both processes
  are gone. Fails today: the step is waited for to its end.
- [ ] AC8: `test_a_runtime_cannot_set_how_a_run_speaks_to_the_engine`: a `runtime.json` whose `env`
  sets `PYTHONIOENCODING` and `pythonunbuffered` is refused with one problem naming both. Fails
  today: both are accepted, then overwritten. Only if the owner says yes to question 2.

## Design

- **`oneframe/processes.py`**, the standard library and `Stopped` only:
    - `Child.start(argv, *, env, cwd=None, stdin="null"|"pipe", stderr="merge"|callable)`, with
      `.pid`, `.lines(stop)`, `.close_stdin()`, `.wait(seconds)` and `.kill(also=())`. `lines`
      yields output until its end and the exit, and raises `Stopped` once `stop` is set, even after
      the output's end. `kill` ends the tree, then the child, closes its stdin, waits a bounded time
      for the child and each pid in `also`, and never raises. A `Child` used as a context manager
      is killed on exit.
    - `run(argv, *, env, on_line, stop, cwd=None)` runs a command to its end or Stop, returning the
      exit code and the last lines; `output(argv, *, env=None, timeout=None)` runs a short command.
      `CompletedProcess` and `TimeoutExpired` are re-exported for the modules that catch or type them.
    - `alive(pid)` (today's `process_alive`) and `wait_gone(pid, seconds)` move here from
      `engine/tests/conftest.py`: `kill` waits on the pids in `also` with them on POSIX.
- **Its users.** `ProcessExecutor` runs one `Child`, stdin piped for the child's watcher and stderr
  to its tail and log; it keeps the job's protocol, passes the pid the child reports to `kill`, then
  removes the job folder with `files.remove_tree`. `runtime_install.run_step` hands its work to
  `run` and `_output` to `output`; `_kill_tree`, `NO_WINDOW`, `NEW_GROUP` and `DROP_ENV` go.
  `bench_fit._holding` is a `Child` whose stderr goes to the tool's own. `hardware` defaults its
  `run` seam to `output`, and `diagnose._uv_version` uses it.
- **`oneframe/environment.py`**, standard library only: `TEMP_VARIABLES`, `ENGINE_PYTHON` (today's
  two lists as one: the executors' `_ENGINE_PYTHON` and the install's `DROP_ENV`, so
  `UV_PROJECT_ENVIRONMENT`, `UV_PYTHON` and `PYTHONDONTWRITEBYTECODE` join it; a run sets the
  last again),
  `SECRETS` (today's `SECRET_OR_UNSAFE_ENV`), `FORCED_ENV`, `LIBRARY_CACHES`, `OVERRIDING_CACHES`,
  `ROOT_ENV`; `inherited(base)`, `child_env`, `uv_environment`, `reserved_env` and `reserved_why`.
  Its users: the executors, the runtime install, `bench_fit`, `scratch`, and `runtimes`, which then
  reads a runtime definition without importing the executors and, through them, the child script.
- **Stop.** Each `should_stop` becomes `stop: threading.Event`: in `child.NodeContext` and `run_node`,
  both executors, `Scheduler.run` and its steps, `runtime_install.install` and `run_step`,
  `archives.download`, and the store's `hash_file` and `adopt`. The server, the runtime manager and
  model jobs pass the `Event`, not `is_set`, and the benches a fresh one; `ctx.stopped()` reads it.
- **Guards.** `faulthandler_timeout` in `engine/pyproject.toml`, under CI's job timeout and far
  above the slowest test. The stdin test's node asks for its standard input's type as the C runtime
  of a DLL numpy loads does at start (the inferred cause): `os.fstat(0)` and `isatty`, and on
  Windows `GetFileType` on the standard input handle. It runs under `stop_after(seconds)`, from a
  new `conftest.py` fixture: an `Event` a daemon timer sets, cancelled when the test ends. The
  child's stdin watcher, the fix, stays.
- **Docs that change:** `docs/architecture.md` (Processes and Environment rows; "killed on Stop"
  becomes "its processes ended on Stop"), `docs/runtimes.md` (`environment.child_env`; the protocol
  variables in the `env` paragraph), `docs/nodes.md` (Stop ends what the node started),
  `docs/roadmap.md` (this spec's items, the Stop hang included, go) and `docs/handoff.md` (the open
  item on Stop and the Windows launcher goes). No rule or command in AGENTS.md changes.
- **Risks:**
    - `taskkill` is a process of its own, a fraction of a second per Stop. If it fails, the
      launcher's job object (unverified) still ends the interpreter, and the wait covers it.
    - Windows ends processes asynchronously: only the interpreter is promised gone when Stop
      returns, and the tests wait for the node's own processes.
    - Only Stop ends a tree: a process a node leaves running after a normal end stays, as today.
      On POSIX a process that starts a session of its own leaves the group, and Stop misses it.
    - In its own session, a node's process no longer gets a terminal's Ctrl+C; the tools' Ctrl+C
      ends it through the same kill (`test_ctrl_c_during_a_run_ends_its_processes`).
    - The hang's cause is inferred, since no trace from the child survived; the stdin test proves
      something only on Windows.

## Tests

- Added:
    - `test_processes.py`: `test_stop_during_a_step_ends_its_whole_tree`,
      `test_every_start_gets_its_own_group_and_no_window`,
      `test_kill_waits_for_the_processes_it_is_told_of`, `test_kill_after_the_end_never_raises`;
    - `test_process.py`: `test_stop_ends_the_processes_a_node_started`,
      `test_ctrl_c_during_a_run_ends_its_processes`, `test_a_node_that_queries_its_stdin_does_not_hang`;
    - `test_child_env.py`: `test_uv_and_runs_drop_the_same_names_in_any_case`;
    - `test_runtime_plan.py`: `test_a_runtime_cannot_set_how_a_run_speaks_to_the_engine`;
    - `test_layout.py`: `test_the_temporary_and_uv_variables_match_the_contract`, and in
      `tests/app/layout.test.js`, "the temporary and uv variables match the contract".
- Changed, names kept:
    - `test_stop_kills_a_runtime_child`: its stop is an `Event` set when the node's stage arrives,
      and the interpreter is gone at return, with no `wait_gone` (AC6);
    - each test passing a stop callable passes an `Event` (`test_process.py`, `test_scheduler.py`,
      `test_archives.py`, `test_errors.py`, `test_child_env.py`, `test_model_store.py`); the two
      scheduler tests whose stop counts its calls set it from their listener instead;
    - `test_child_env.py`, `test_child.py`, `test_process.py` and `test_footprint.py` import from
      `environment`, and `conftest.py` takes `alive` and `wait_gone` from `processes`.
- Removed: none.

## Verification

AC1 to AC3 are commands run in the PR. AC4 to AC8 and every test above run in CI on ubuntu-latest
and windows-latest; AC6 and the stdin test say something only on Windows. Requirement 1's flags and
requirements 6 and 7 are checked by the tests under Added. No step needs a GPU or a local session.

## Decisions taken

- `threading.Event` is the one stop type: callers make one already, and the transport sleeps on it.
- Every child gets its own session or process group, node runs included, so its tree ends as one.
- POSIX kills a group only while its leader is unreaped, so a reused group id is never hit.
- Windows ends a tree with System32's `taskkill /T /F`: a job object of our own needs a suspended start.
- Windows waits on a handle to the reported interpreter, opened before the kill, so no pid is reused.
- Names are compared upper-cased on every system: Windows ignores case, and one rule beats two.
- uv and runs drop one list, secrets included: uv needs no hub token either.
- uv also loses the engine's folders on `PATH`: it is started by its full path.
- `child_env`, `uv_environment` and `run_step` (a test's seam) keep their names: fewer test edits.
- `uv_environment` keeps its signature here: 011 changes it once, to take the layout's folders.
- A `Child` knows nothing of jobs, so spec 004's worker can be one long-lived `Child`.
- A job folder that will not go is logged and left to the next sweep, never hiding how a run ended.
- JavaScript keeps its own copy (it cannot import Python), held equal by `contracts/layout.json`.
- `faulthandler_timeout`, not pytest-timeout: it is built into pytest, so no new dependency.

## Out of scope

- The endings sent before their place is let go, and `Emit` defined once (010).
- The node API module, the typed job, and `allow_network` and `exit_with_parent` (009).
- The executor provider, `on_pid`, and `CUDA_VISIBLE_DEVICES=-1` per process (010).
- uv's folders taken from the layout, and runtime sources through the transport (011).
- `bench_fit` naming the torch runtime (013).
- The test guard (007); the tests' wall-clock bounds, even in tests this spec changes (014).
- How the app and `scripts/tool.js` start processes, in JavaScript: unchanged.

## Open questions (answered by the owner, 2026-10-18)

1. **Does Stop end the processes a node started?** Today it kills the node's own process only, and
   on Linux a process the node started keeps running. *Answered: yes.* executors.py already
   promises a kill "outright", and a process left behind can hold the card.
2. **May `runtime.json` set `PYTHONIOENCODING` or `PYTHONUNBUFFERED`?** Today both are accepted, then
   silently overwritten; no runtime sets them. *Answered: refuse them*, as the cache variables
   are: a setting that cannot take effect should say so. `PYTHONPATH`, `PYTHONHOME` and
   `PYTHONPYCACHEPREFIX` stay accepted and applied, as today: an author sets them on purpose.
