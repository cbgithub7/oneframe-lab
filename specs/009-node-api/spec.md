# 009: The node API

Status: draft
Owner approval: (date, once approved)

The third of seven clean-up specs (007 to 013), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
node contract. It lands after 008, whose `stop` it takes, and before 010, which makes the executor
swappable. The node contract changes once, here, before any model node depends on it.

## Problem

- The child script is also the node API: `errors.py`, `cache.py`, `scheduler.py` and
  `executors.py` import it, while node code in a runtime can import none of it.
- A job is a dict built in three places (the scheduler and both benches) and read untyped;
  `allow_network` is read and never set.
- Node code is imported with its folder put first on `sys.path`, for good. A helper `utils.py`
  hides a runtime's `utils` package, and two engine nodes that import same-named helpers both run
  the first one's (checked).
- The contract is `run(ctx)` alone, so spec 004 would rewrite every node to keep a model loaded.
  `ctx` has no type, every node declares it `Any`, and seven of its public members are not in
  docs/nodes.md: `job`, `node`, `out_dir`, `outputs`, `ways`, `peaks()` and `stopped()`.

## Requirements

1. **One node API, apart from the child script.** `oneframe/node/`, standard library only and
   checked for Python 3.11, holds what node code uses (`Context`, `LoadContext`, `Value`,
   `Stopped`, `NodeFailed`), `Emit`, and what runs a job. The engine imports it, never `child.py`.
2. **The child is a bootstrap.** `child.py` stays at its path, a short script that makes only
   `oneframe.node` importable from the engine, sets up the process's guards, and runs the job. Node
   code may import `oneframe.node` and no other engine module (open question 1).
3. **A typed job.** A frozen `Job` and its result, `Done`, are the only form a job takes, and
   their JSON is written and read in one module. The scheduler builds a job in one method, each
   bench builds its own, and both executors take a `Job` and return a `Done`. `allow_network` is
   deleted, `exit_with_parent` becomes the child's `--exit-with-parent` argument, and the job's
   `env` goes: `CUDA_VISIBLE_DEVICES=-1` belongs to the process that runs it.
4. **Node code is a package named for the node,** its entry a module in it, and `sys.path` never
   changes. A node imports its helpers relatively, so `import utils` reaches the runtime's package
   (open question 3). A rerun in the engine forgets the package's modules, so its code runs as it
   is now.
5. **`load`, then `run`.** A manifest may name `run.load` (`file.py:function`), as it names
   `run.entry` (open question 2). Each attempt then calls `state = load(...)` once, then
   `run(ctx, state)`; without it, `run(ctx)` as today. `load`'s context has no inputs, paths or
   outputs, so what it returns can outlive one run when spec 004 keeps it.
6. **`ctx` is typed.** `Context` and `LoadContext` are Protocols holding exactly what
   docs/nodes.md documents; the context's other members become private (open question 4). Every
   node, probe and test node file in the repo annotates `ctx` with them.
7. **Nothing a person sees changes.** A run's events, outputs and failures are as today.

## Acceptance criteria

- [ ] AC1: No engine module or test imports the child script:
  `rg -n 'oneframe\.child|from oneframe import .*\bchild\b' engine/src engine/tests` prints
  nothing. Today it prints seven lines, in six files.
- [ ] AC2: In a runtime child, node code can import `oneframe.node`, and not `oneframe.scheduler`
  or the top-level `models`, `graph`, `errors`, `layout`, `scheduler` or `child` (the extended
  `test_a_runtime_child_cannot_import_the_engines_own_modules`, on the engine's own Python, where
  `oneframe` is installed). Fails today: there is no `oneframe.node`; `oneframe.scheduler` imports.
- [ ] AC3: Two engine nodes, each importing its own `utils.py` relatively, each run their own and
  leave the engine's `sys.path` as it was
  (`test_two_engine_nodes_with_same_named_helpers_each_run_their_own`). Fails today: a relative
  import has no package to be relative to, and each folder stays on the path.
- [ ] AC4: A runtime node reaches its helper `utils.py` with `from . import utils`, while
  `import utils` reaches the runtime's package of that name, given on `PYTHONPATH`
  (`test_a_runtime_nodes_helper_never_hides_a_package_of_its_runtime`). Fails today: the relative
  import has no package, and `import utils` finds the helper.
- [ ] AC5: For a node that names `run.load`, run through the scheduler, `load` runs once and
  `run(ctx, state)` gets what it returned, in the engine's process and in a runtime child
  (`test_load_runs_once_and_run_gets_its_state`, one case per `where`). Fails today: `run` is
  called with `ctx` alone.
- [ ] AC6: The context `run` gets and the one `load` gets each have exactly the public members of
  their Protocol, `Context` and `LoadContext` (`test_a_context_shows_exactly_its_protocol`, one case
  for each). Fails today: the context has the seven undocumented members, and nothing for `load`.
- [ ] AC7: A job's JSON is written and read in one module:
  `rg -n '"out_dir"' engine/src engine/tests --glob '!**/node/job.py'` prints nothing. Today it
  prints nine lines: the scheduler, both benches, the child, and five jobs the tests build.
- [ ] AC8: Every node, probe and test node file in the repo types its context (not `Any`, not bare):
  `rg -nP 'def (run|load)\(ctx(?!: (Load)?Context\b)' nodes runtimes engine/tests/hardware engine/tests/runtimes`
  prints nothing. Today it prints six lines.

## Design

**`engine/src/oneframe/node/`**, which `ruff.toml` and `pyrightconfig.json` check for 3.11, as
they check `child.py` now. Today's code moves with its behaviour:

- `__init__`: `Value`, `Stopped`, `NodeFailed`, `Emit`; `LoadContext`, the documented members but
  `inputs`, `path()` and `output()`; and `Context`, which adds those three.
- `job.py`: `Job`, today's keys plus the code's folder and `load`, less `env`,
  `exit_with_parent` and `allow_network`; and `Done` (outputs, seconds, peaks, ways, stats).
- `context.py`: today's `NodeContext`, with what the runner needs (outputs, ways, peaks, the view
  `load` gets, the `Done`) kept private.
- `failures.py`: `NetworkForbidden`, `classify`, `describe`, `is_oom`, `is_torch_oom`.
- `usage.py`: resident memory, the card's peaks, releasing a failed way, and the allocator's cap.
- `code.py`: loads the folder as package `oneframe_nodes.<id as a name>_<8 hex of the folder's
  normalised path>`, purges it by name, and returns the entry's `run` and optional `load`.
- `process.py`: the once-per-interpreter guards: the protocol's descriptor and the pid event,
  ending with the parent, the network guard, and the tqdm patch (it reads the current job's stage).
- `runner.py`: `run_job(job, say, stop, process=None) -> Done`. It caps the allocator (in a child
  only), builds the context, loads the code, calls `load` and `run`, and makes the `Done`. A
  failure goes up with the run's peaks, which the child's `error` event carries as today.

**`child.py`** keeps its path, since the reviews name it. Its `main()` registers a stub `oneframe`
package with an empty path, then `oneframe.node` from the `node/` folder beside it; parses
`[--exit-with-parent] job.json`; and runs the guards, `run_job` and the error event. Nothing is
registered at import.

**The engine.** `executors.py` finds `child.py` by path and stops importing it;
`child_command(python, job, exit_with_parent=False)`; `ProcessExecutor` passes the flag. The
scheduler builds its `Job` in one `_job` method, reads the `Done`, and its `_executor(manifest,
device)` gives a runtime node on the processor `CUDA_VISIBLE_DEVICES=-1`, until 010's provider
takes that over. `errors.py`, `cache.py` and `scheduler.py` import from `oneframe.node`. `manifest.py`
parses `run.load` as it parses `run.entry`, with the same refusals. `pyrightconfig.json`'s include
gains `runtimes`, so the torch probe's `ctx` is checked too.

**Docs that change:** [docs/nodes.md](../../docs/nodes.md) types `ctx`, shows `run.load`, `load`
and `run`, imports helpers relatively, and lets node code import `oneframe.node` (it says "never
imports the engine" today); the Executors row of [docs/architecture.md](../../docs/architecture.md)
names `oneframe/node/`; [docs/roadmap.md](../../docs/roadmap.md) loses the node contract,
`child.py`'s five concerns, a job's dict, `allow_network`, and the typed job and the child's split
(010 takes the rest of "Ready for spec 004"). No rule or command in AGENTS.md changes.

**Risks:**

- In the engine's process, the stub would replace the real `oneframe`, and the guards would close
  its downloads. Only `child.py`'s `main()` sets them up, nothing imports it (AC1), and a test
  checks that an engine run leaves the sockets alone.
- A node that imports a helper absolutely breaks. No node in the repo has one; three test nodes
  (in `test_child_env.py`, `test_footprint.py` and `test_cache_keys.py`) change to a relative import.
- A process a node spawns (a DataLoader worker) cannot import the node's modules by name: today
  `node.py` already, and now its helpers too, which the path made importable. Spec 005 should know.
- `bench_fit`'s context probe runs on `cpu` but needs the card visible: the `-1` is set only for
  nodes the scheduler runs. That path runs only on a card (Verification).

## Tests

- Added:
    - `test_process.py`: `test_two_engine_nodes_with_same_named_helpers_each_run_their_own`,
      `test_a_runtime_nodes_helper_never_hides_a_package_of_its_runtime`,
      `test_load_runs_once_and_run_gets_its_state`, `test_an_engine_run_leaves_its_sockets_alone`;
    - `test_node_api.py`: `test_a_context_shows_exactly_its_protocol`, and
      `test_a_job_survives_its_json` (a param that is not plain JSON is written as its `str`);
    - `test_ports_and_manifests.py`: `test_run_load_is_refused_as_run_entry_is`.
- Renamed: `test_a_job_brings_its_own_environment` -> `test_a_processor_run_sees_no_card_and_a_card_run_sees_its_card`
  (a job carries no environment now, so one runtime node runs on a card, then on the processor).
- Changed, names kept: in `test_process.py`, the child-imports test (AC2) and the two
  exit-with-parent tests (the argument); in `test_child.py`, the fallbacks tests (they read the
  `Done`) and the resident-memory test (`usage`); in `test_errors.py`, the one-`Stopped` and
  undeclared-kind tests; the three test nodes with a helper (Risks), among them
  `test_an_engine_node_runs_its_helpers_as_they_are_now`'s. Every job a test builds is a `Job`.
- Removed: none.

## Verification

AC1, AC7 and AC8 are commands run in the PR; AC2 to AC6 and the tests above run in CI on
ubuntu-latest and windows-latest, and `bench:runtime`'s probe through the tiny runtime, on Python
3.11 without the engine. No GPU step here: the cap's code moves unchanged and its tests use a
stand-in torch. The benches' probes run on a card once, after 010, which changes them again.

## Decisions taken

- `child.py` bootstraps a stub `oneframe` rather than a second top-level package: packaging stays.
- The package name hashes the folder's normalised path: one folder is one package on Windows too.
- `code.py` hashes that path with `hashlib` itself: node code cannot import `files.py` (011).
- `--exit-with-parent` is an argument, off by default: tests start the child without a stdin pipe.
- `allow_network` is deleted: nothing sets it.
- The job loses `env`: a card is hidden per process, which 010's provider and spec 004 own.
- The benches' run on a card waits for 010, which changes their probes again: one run, not two.
- `Job` and `Done` carry no format number: one engine writes and reads them within one run.
- `load` gets a view without inputs, paths or outputs: its state can then be kept (spec 004).
- The runner, not a public `ctx` method, makes that view and the `Done`: `ctx` stays its Protocol.
- `NodeFailed` and `Stopped` are exported for a node's `except` to let through; `ctx.fail` stays.
- Precision still defaults to `fp32` and `seconds` spans load and run: a person sees no change.
- `Stopped` stays a `RuntimeError`: a new base would touch every `except Exception` in the engine.
- No `.pyi` stub and no published node API package: nodes outside the repo are not a v1 target.

## Out of scope

- The executor Protocols, `OneProcessPerRun` (which takes over the `-1`), the shared probe helper,
  `on_pid`, `Emit` imported from `oneframe.node` everywhere, and the scheduler's long functions (010).
- Processes, children's environments and the one stop type (008, which lands first).
- The test guard, which reads this spec's Renamed line (007); files and hashing (011); other
  modules and the rest of the dead weight (012); the tests' wall-clock bounds (013).
- Keeping `load`'s state between runs, and the key it is kept by (spec 004).

## Open questions

1. **May node code import `oneframe.node`?** `docs/nodes.md` says node code never imports the
   engine. *Recommended: yes, that package and nothing else:* the child brings it into every
   runtime, and it gives nodes a type and `Stopped`.
2. **Is `load` named in `node.json`** (`run.load`, an added optional field), rather than found by
   its name in the module? *Recommended: named:* a module-level `from torch import load` would
   otherwise be called.
3. **Do nodes import their own helpers relatively?** It follows from loading node code as a
   package, the review's fix. *Recommended: yes:* then no helper hides a runtime's package, and no
   two nodes' helpers meet. No node in the repo has a helper yet.
4. **Is the public `ctx` exactly what docs/nodes.md documents?** `job`, `node`, `out_dir`,
   `outputs`, `ways`, `peaks()` and `stopped()` leave it, and `check_stop()` stays the one way to
   ask about Stop. *Recommended: yes:* only tests read them, and no model node depends on them yet.
