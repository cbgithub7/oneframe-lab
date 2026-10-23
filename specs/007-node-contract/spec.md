# 007: The node contract, once

Status: draft
Owner approval: (date, once approved)

The first of the clean-up specs that the [review of 2026-10-12](../../docs/reviews/2026-10-12.md)
asked for before the first model node (step 2b in the [handoff](../../docs/handoff.md)). It takes the
roadmap's node contract and "ready for spec 004" items, with the parts of others that live in the
same code. The rest of the clean-up follows in three more specs (Decisions taken).

## Problem

Spec 005 is about to write the first model nodes against a contract that is not finished:

- In the engine's process, a node's `import utils` can run another node's `utils.py`. In a runtime,
  a node's folder comes first on the import path and hides a runtime package of the same name.
- Spec 004 would have to rewrite every node: there is only `run(ctx)`, so nothing says which part
  loads a model and may be kept, or which settings that part reads.
- The context has public parts nobody documented, and nodes type it as `Any`. `Value` and
  `Stopped` live in the child script, which `errors.py`, `cache.py` and `scheduler.py` import.
- A job is a dict, built in three places and read untyped. The scheduler builds its executors
  itself, so spec 004's worker cannot be put in their place.

## Requirements

1. **A node's code is a package of its own.** It is imported as a package named for the node, and
   its folder is never on the import path: helpers are imported relatively (`from . import utils`).
   Two nodes' same-named helpers never meet, in the engine's process or in a child. A node's
   modules are forgotten before each job.
2. **One node API, apart from the child.** `oneframe/node.py`, standard library only and Python 3.11,
   holds `Value`, the two contexts, `Stopped`, `NodeFailed` and the job. Engine modules import it;
   only `executors.py` names the child script. In a runtime child it is the only part of the engine
   a node can import.
3. **Load, then run.** A node may define `load(ctx)`, whose return is its state; it is then called
   `run(ctx, state)`. A node without `load` keeps `run(ctx)`. `load`'s context gives the device, the
   precision, the attempt, the node's files, and only the params marked `"load": true` (the
   `files_by` param always is), with `stage`, `progress`, `fail`, `check_stop`, the memory budget
   and `fallbacks`. Reading any other param in `load` fails the node with kind `contract`, naming
   it. Here `load` and `run` run in one process per run, and nothing is kept after it.
4. **The context says what it is.** Every public name on either context is in
   [nodes.md](../../docs/nodes.md), and every name there exists. `job`, `outputs`, `ways`, `out_dir`,
   `node` and `peaks()` become private, and `stopped()` goes (`check_stop()` is the one way). Nodes
   type their contexts as `Context` and `LoadContext`, and pyright checks them.
5. **One typed job.** A `Job` is built in one place, which the scheduler and both benches use, and
   crosses to a child as JSON. It holds only what the node's process reads: an executor's own
   settings (the environment, ending with the parent) are not in it, and `allow_network` goes.
6. **The scheduler is handed its executors.** A provider gives the executor for each node; the
   default is today's (the engine's process, or a child per run). The six settings the scheduler
   takes only to build children move into that provider.
7. **The child is a bootstrap and a job.** Once per process: the protocol stream, its process id,
   ending with the parent, the closed network, tqdm. Per job: the allocator cap, the node's import,
   `load`, `run`, and the `done` or `error` event. A process still runs one job, but the job code
   can run twice in one process, as spec 004's worker will.
8. **Load is timed apart.** `node.done` carries `load_seconds` beside `seconds` (which still covers
   the whole job), so steps 3 and 4 measure the load times spec 004 is sized by.

## Acceptance criteria

- [ ] AC1: Two nodes whose folders hold different `utils.py` files run one after the other, each
  with its own, in the engine's process and in one child process given both jobs (tests).
- [ ] AC2: In a runtime child, node code imports `oneframe.node`, and `import oneframe.graph` raises
  `ModuleNotFoundError` (process test with the tiny runtime).
- [ ] AC3: A runtime node's `load` runs before `run` in the same process, and `run` receives the
  object `load` returned (process test).
- [ ] AC4: `load` reading a param not marked `"load": true` fails the node with kind `contract`,
  naming the param (test).
- [ ] AC5: A scheduler given a provider whose executor records jobs runs a graph with a runtime node
  without starting a process, and each job it records is a `Job` (unit test).
- [ ] AC6: The public names of `Context`, `LoadContext` and `Value` equal the names
  [nodes.md](../../docs/nodes.md) documents, both ways (test).
- [ ] AC7: `node.done` for a node with `load` carries `load_seconds`, no larger than `seconds`
  (test).
- [ ] AC8: **Hardware.** Spec 002's hardware check passes on this branch, as it did before the
  child's split (a `bench:fit` report from a real card, naming it).

## Design

**Files.**

- `oneframe/node.py` (new, checked as Python 3.11 like `child.py`): what a node sees. `Value`,
  `LoadContext` and `Context` (which adds the inputs, every param, `path` and `output`), `Stopped`,
  `NodeFailed`, the `Job` dataclass, and the memory measures the contexts use.
- `oneframe/child.py`: only the process a node runs in. `main()` sets up the bootstrap, then
  runs the job; the per-job code is one function the engine's own executor calls too. The error
  mapping (`classify`, `describe`) stays here.
- `oneframe/executors.py`: an `Executor` protocol (`execute(job, emit, should_stop)`) and the default
  provider, holding the runtime's interpreter, environment, caches, log folder, `on_pid` and
  temporary folder, which `Scheduler.__init__` no longer takes.
- `oneframe/scheduler.py`: builds the `Job`, asks the provider for an executor. `_attempt` loses the
  job-building block.
- `oneframe/manifest.py`: a param may say `"load": true`; the `files_by` param is one implicitly.
- `oneframe/errors.py`, `cache.py`: import from `node.py`. `bench_fit.py`, `bench_runtime.py`: the
  `Job` builder.
- `nodes/*/node.py`: typed contexts. Test nodes that put their own folder on `sys.path` import their
  helpers relatively instead.
- Docs: `docs/nodes.md` (the contract, `load`, what each context holds, how helpers are imported),
  `docs/architecture.md` (the parts table), `docs/runtimes.md` (how the child starts).

**One API in two places.** The engine imports `oneframe.node` as part of its package. A runtime has
no engine installed, so the child, started by path with `-P`, loads `node.py` from its own folder
under the same name, inside an `oneframe` package of its own whose search path is empty. Node code
then writes `from oneframe.node import Context` wherever it runs, pyright resolves it from the
engine, and no other engine module can be imported in a runtime.

**Packages named for nodes.** Each node's folder is imported as `oneframe_nodes.<node>` (its id,
escaped to an identifier), whose search path is the folder alone; `entry` names a module in it.
Forgetting a node's modules is then by name, not by file.

**What `load` sees is what spec 004 keys by.** A kept state is valid only for the node's code and
runtime build, the device, the precision and the params `load` read. Marking those params in the
manifest, and refusing the others in `load`'s context, makes that set known before spec 004
exists, without a node changing when 004 starts keeping state. A fit that changes a load param
(say, `offload`) means a reload, which spec 004 then sees from the key.

**Out-of-memory.** `load` runs under the same cap and fit as `run`. Running out in either is `oom`,
answered as today: once more, in a new process, with a smaller fit. `fallbacks` works in both.

**Risks.**

- A runtime's Python older than the engine's: `node.py` is held to 3.11 by ruff and pyright, and the
  tiny runtime (3.11) runs every process test.
- A node that imported helpers absolutely now fails: only test nodes do, and they change with it.
  `nodes.md` says how, and the failure is `missing`, naming the module.
- The cap moves into per-job code: AC8 runs it on a card.

## Tests

- Added: AC1 to AC7. Also: only `executors.py` imports `oneframe.child`; a `Job` crosses to a child
  and back unchanged; the default provider gives the engine executor for an engine node and a
  process executor for a runtime node.
- Changed: tests that read `ctx.job`, `ctx.ways` or `ctx.peaks()` read the `done` payload instead;
  test nodes import helpers relatively.
- Removed: none

## Verification

- AC1 to AC7: the tests above, in CI on Windows and Linux.
- AC8, in a local session (`/local-session`) on any machine with an NVIDIA card, on this spec's
  branch: `npm run bench:fit -- --out specs/007-node-contract/reports/` (spec 002's hardware check,
  in the torch runtime), then commit the report with a verdict, as
  [spec 002's](../002-fit-to-memory/reports/2026-10-02-ac8.md) was. It passes when the cap is applied
  and both overruns end as they did there: `node.step_oom` answered by `fallbacks`, and `node.oom`
  answered by the engine's retry.

## Decisions taken

- The clean-up is four specs: this one, then the modules split, then one way to do each thing, then
  the tests; the roadmap's items cannot fit one spec's eight criteria.
- This spec takes the roadmap items that live in the same code: `child.py`'s concerns, the job's
  dict, `allow_network`, and `Scheduler.__init__` and `_attempt`.
- `run(ctx)` stays for a node without `load`: nodes that load nothing do not change.
- `load` is found by name in the entry's module: no manifest field for a function.
- Load params are marked in the manifest, not inferred: inference cannot see what code reads.
- In a child, `oneframe` holds only `node`: one import line for nodes everywhere, and no way to the
  rest of the engine.
- `stopped()` goes: a node in the engine's process ends early only by `check_stop()`.
- `seconds` keeps its meaning (the whole job), so what each machine learned stays valid.
- The job is a dataclass, not a TypedDict: the child checks it once where it is read.

## Out of scope

- Keeping a state between runs, and the worker per runtime: spec 004.
- The rest of the clean-up ([roadmap](../../docs/roadmap.md), "For the clean-up specs after 007"):
  the module splits, one way to do each thing, and the tests, each its own spec.
- Recipes, the photo's token and results by key: spec 005.

## Open questions

None.
