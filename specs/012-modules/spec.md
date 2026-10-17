# 012: Modules that are several, and long functions

Status: draft
Owner approval: (date, once approved)

The sixth of seven clean-up specs (007 to 013), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
"Modules that are several", "Nothing hard-coded", "Long functions" and "Dead weight" items. It
lands after 008, 010 and 011, which change `runtimes.py` and `runtime_install.py` first, so each
file moves once, as those specs leave it.

## Problem

- `oneframe/memory.py` (1,430 lines) is five modules: the model and its parser, the settings, the
  estimate, the fit and the learned store. `oneframe/runtimes.py` (1,071) is three: definitions,
  the plan and the manager, with the install beside it. Reading a `node.json` loads all of memory,
  and with it `files`, `layout`, `errors` and, today, the child script.
- A malformed `node.json` is accepted or crashes (each case checked). Accepted: no `version` or
  `title` (read as `""` after the problems check), `devices` as an object, a `run.runtime` or
  `summary` that is not text. A `run`, `licence` or `links` that is not an object, or `devices` as a
  number, raises what `registry.discover` does not catch, and the engine fails to start; a
  `runtime.json` not in UTF-8 does the same in `runtimes.discover`.
- Reading a definition is written twice (the text field, the id rule, the error class, the file,
  the discover loop), and versions compare two ways (`memory._version`, `runtimes.version_tuple`).
- `install` is 179 lines with four closures; `manifest.parse` is 118 with two.
- `bench_fit`, shipped in the package, names the runtime `torch` eight times and reaches into
  `engine/tests/hardware` for spec 002's AC8 scenario. No test checks "Nothing hard-coded".
- Manifests accept the devices `mps`, `xpu` and `rocm`, and `runtime.json` the vendors `amd` and
  `intel` and the OS `macos`, all outside v1's platforms.

## Requirements

1. **Memory is a package,** `oneframe/memory/`: `model`, `settings`, `estimate`, `fit` and
   `learned`. `model` imports only the standard library and `oneframe.reading`; reading a manifest
   loads it alone of the five.
2. **Runtimes is a package,** `oneframe/runtimes/`: `definition`, `plan`, `manager` and `install`
   (today's `runtime_install.py`). Planning loads neither the manager nor the install.
3. **A package's root holds only its docstring,** as `oneframe/models/` does: callers import its
   modules, and nothing is re-exported, so a light module stays light.
4. **One way to read a definition.** `oneframe/reading.py`, standard library only, holds the text
   field, the id rule, the number test, the version compare, reading a JSON object from a file, one
   `ProblemsError`, and the discover loop. Manifests, memory models and runtimes use it.
5. **A malformed definition is a problem, never a crash** (open question 2). `parse` reads every
   field before it decides, and a field of the wrong type is a problem naming it.
6. **`parse` and `install` in named steps,** each under 60 lines, behaving as today.
7. **Nothing in the engine names a runtime, a node or the tests.** `bench_fit` measures a node a
   person names. Spec 002's AC8 scenario moves to `engine/tests/fit_check.py`, run by
   `npm run bench:fit-check` (open question 4), and takes its runtime from its node's manifest. A
   test holds the rule for the engine.
8. **Names outside v1 are refused** (open question 1): a manifest's devices are `cpu` and `cuda`;
   a build's vendor is `nvidia` or `none`, and its OS `windows` or `linux`.
9. **Nothing else a person sees changes:** events, records, `Fit.to_json`, messages and logger
   names stay byte for byte.

## Acceptance criteria

- [ ] AC1: `test_reading_a_manifest_loads_only_the_memory_model`: in a fresh interpreter, importing
  `oneframe.manifest` loads no other module of `oneframe.memory`, and none of `files`, `layout` or
  `errors`. Fails today: it loads all of memory, and the child script through `errors`.
- [ ] AC2: `test_planning_loads_neither_the_manager_nor_the_install`: in a fresh interpreter,
  importing `oneframe.runtimes.plan` loads neither `oneframe.runtimes.manager` nor
  `oneframe.runtimes.install`. Fails today: there is no plan module.
- [ ] AC3: `test_a_malformed_definition_is_a_problem_never_a_crash`: each case in Problem, through
  the one `discover`, is listed as a problem naming what is wrong, and a good sibling still loads.
  Fails today: some cases are accepted, and the others crash.
- [ ] AC4: `rg -n 'class (ManifestError|DefinitionError|ProblemsError)\(ValueError\)|re\.compile\(r"\^\[a-z0-9\]\+\(\?:|def (_?text\((data|key)|_?number\(value|_version\(|version_tuple\()|is already used by|should hold one JSON object' engine/src/oneframe`
  prints only lines in `engine/src/oneframe/reading.py`. Today it prints 13, in `manifest.py`,
  `memory.py`, `registry.py` and `runtimes.py`.
- [ ] AC5: No function in `manifest.py` or `runtimes/install.py` reaches 60 lines
  (`end_lineno - lineno + 1` from Python's `ast`, measured once and pasted in the PR). Today
  `install` is 179 and `parse` 118.
- [ ] AC6: The node test "a review may name a path that has since moved, and its links are still
  checked" (`tests/app/guards.test.js`). Fails today: the docs check fails such a review. Only if
  the owner answers (a) to question 3.
- [ ] AC7: `test_no_engine_module_names_a_runtime_a_node_or_the_tests` (`test_rules.py`): no engine
  module holds, outside a docstring, a repo runtime's or node's id (but for library names the test
  lists by file), or `tests` as a path part. Fails today on `bench_fit.py`.
- [ ] AC8: The node test "bench:fit-check runs spec 002's check from the tests folder"
  (`tests/app/tool.test.js`): `package.json`'s `bench:fit-check`, run with `--data <tmp>
  --runtimes <empty>`, exits 0 with a report that says "Not measured". Fails today: no such script.
- [ ] AC9: `test_names_outside_v1_are_refused` (`test_rules.py`): the devices `mps`, `xpu` and `rocm`
  in a manifest, and the vendors `amd` and `intel` and the OS `macos` in a build, are each refused
  with a problem. Fails today: all are accepted. Only if the owner says yes to question 1.

## Design

**The packages,** moved as they are. Memory: `model` (the model and its parser), `settings` (the
settings and margins), `estimate` (`Target`, `Device`, `Learned`, `settings_hash` and the estimate),
`fit` (`Checkpoints` and the fit) and `learned` (the store). Runtimes: `definition`, `plan` (`_card`
becomes `planned_card`, which the manager asks too), `manager` (with 010's `RuntimeSide` and
`NoRuntimes`) and `install`.

**`oneframe/reading.py`**: `text`, `check_id`, `number` (memory's `_number`), `version_tuple`,
`at_least` and `at_most` (which `Precision.runs_on_card` uses too), and `read_object(path, error)`:
the object and its bytes, or `error(source, problems)` for a file that cannot be read, decoded or
parsed, or holds no object, so `models/pin.py`'s `except ManifestError` still holds. `ManifestError`
and `DefinitionError` subclass `ProblemsError`. `discover(roots, filename, load)` serves `registry`
and `runtimes.definition`, and lists any `ProblemsError` as a problem.

**`manifest.parse`** calls one function per part (the head, ports, params, run, devices, memory and
files, licence and links), each adding its problems, then raises once. **`install`** keeps the
signature 008 and 011 leave it. Each `STEPS` entry becomes a function of one `_Install`, which
holds the runtime, the build, the paths, uv's environments (built once) and the step, log and run
helpers that are closures today. Sync goes through `run`, with today's message. `install`,
`_freeze` and `run_step` stay module-level and are called through the module: tests patch them.

**`bench_fit`** keeps measuring a named node (`parse_sets`, `measure`, `make_scheduler`,
`bench_node`, its report), and its `node` argument becomes required. **`engine/tests/fit_check.py`**
takes spec 002's scenario: `Ac8`, `find_ac8`, `three_settings`, the holder, the overrun, `cap_refusal`
and their report. It finds `hardware/` beside itself, takes its runtime from its node's manifest,
and probes through 010's `run_probe`. **`scripts/tool.js`** runs an argument ending in `.py` as a
repo file, by its absolute path, and anything else as a module, as today.

**The rule's test** walks each engine module's AST; its exceptions (docstrings, library names such
as `torch` that are not runtime ids) live in the test, each with its file.

**Names outside v1** (question 1): `VENDORS`, `OSES` and the manifest's devices narrow; memory's
paths for devices it "does not read yet" (the parser's and the fit's) and the plan's other-vendor
refusal go. `hardware.os_name` still says `macos`: it reports the machine as it is.

**Docs that change:** `AGENTS.md` Commands (`bench:fit` needs a node; a `bench:fit-check` line; the
tools that start through `tool.js`; with (a) to question 3, the docs check's line); in
`docs/architecture.md`, the Manifests, Registry, Memory, Runtimes and Benches rows; `docs/nodes.md`'s
`bench:fit` section and devices; `docs/runtimes.md`'s vendors and OSes; and `docs/roadmap.md`, whose
items go as each module moves (the docs check reads their paths). With (a), `scripts/check-docs.js`
skips backticked paths in `docs/reviews/`, as in specs, and still checks their links.

**Risks.** `from oneframe.memory import fit` is the module: pyright flags it called as a function,
and ruff a local that shadows an import (`margin = margin(...)`). `runtimes.definition` imports
neither the manager nor the executors, and 010's provider imports `RuntimeSide` under
`TYPE_CHECKING` only, so no cycle forms.

## Tests

- Added:
    - `test_server_and_nodes.py`: `test_reading_a_manifest_loads_only_the_memory_model`;
    - `test_runtime_plan.py`: `test_planning_loads_neither_the_manager_nor_the_install`;
    - `test_ports_and_manifests.py`: `test_a_malformed_definition_is_a_problem_never_a_crash`;
    - `test_rules.py`: `test_no_engine_module_names_a_runtime_a_node_or_the_tests`, and
      `test_names_outside_v1_are_refused` (question 1);
    - `guards.test.js`: "a review may name a path that has since moved, and its links are still
      checked" (question 3);
    - `tool.test.js`: "bench:fit-check runs spec 002's check from the tests folder", and "a tool
      given as a .py path runs that file, by its absolute path".
- Moved, names kept: the five tests of spec 002's scenario, `test_bench_fit.py` to `test_fit_check.py`.
- Changed, names kept:
    - the tests' imports of the two packages, the install module imported as `runtime_install` so
      monkeypatch targets hold;
    - in `tool.test.js`, the npm-script list takes the fit check's path, and the TMP test runs it;
    - with question 1, `test_every_problem_in_a_definition_is_listed_at_once` expects the narrowed
      lists.
- Renamed: none.
- Removed: `test_devices_the_engine_does_not_read_yet_are_named`, only if the owner says yes to
  question 1: the path it tests is gone, and AC9's test checks the refusal that replaces it.

## Verification

AC4 and AC5 run in the PR, with AC5's measure pasted there. AC1 to AC3, AC7, AC9 and the pytests
above run in `npm run engine:check`, and AC6 and AC8 (`tool.js`'s `.py` path) in `npm run check`,
on ubuntu-latest and windows-latest. The moved scenario's card path runs nowhere else, so after
the merge a local session (`/local-session`) on an NVIDIA card runs `npm run bench:fit-check` once;
its report, naming the machine, goes to `specs/012-modules/reports/<date>-local.md`.

## Decisions taken

- `Target` and `Device` go with the estimate, and `Checkpoints` with the fit: where each is used.
- `learned.py` logs as `oneframe.memory`, so logs and diagnostics read as before.
- `ManifestError` and `DefinitionError` stay, under one `ProblemsError`: callers catch them by name.
- Nodes and runtimes are found by one `discover`: one walk, one problem and duplicate-id rule.
- `pin.find_manifest` keeps its own walk: it must find a manifest the check would refuse.
- One version compare for capabilities, drivers and the torch floor: `"8"` and `"8.0"` now agree.
- `fit_check.py` lives in `engine/tests/`, not in `hardware/`, which ruff and pyright treat as 3.11.
- `tool.js` takes a `.py` path besides a module, rather than the tests becoming a package.
- `runtimes.plan` (94 lines) and `graph.plan` (100) stay: pure, tested, and not flagged.

## Out of scope

- The scheduler's long functions and parameters, the executor provider, and `RuntimeSide`'s
  design (010); this spec only moves it.
- The test guard (007); processes and environments (008); the node API and `allow_network` (009);
  files, hashes, downloads and the layout's paths, the install's included (011); the tests'
  clean-up (013). "Nothing hard-coded" in JavaScript: the app names no node today.
- macOS, AMD and Intel, which the platform matrix leaves out of v1.

## Open questions

1. **Are names outside v1 refused?** Manifests then name only `cpu` and `cuda`, and builds only
   `nvidia` or `none`, on `windows` or `linux`. *Recommended: yes:* nothing uses them, they are
   outside the platform matrix, and widening later still reads every file written now.
2. **Is a malformed `node.json` a problem, shown beside the catalogue,** in each case in Problem?
   *Recommended: yes, as bug fixes:* today it is listed untitled or stops the engine, and no node
   in the repo is affected.
3. **How does the docs check treat a review that names a moved module?** The 2026-10-12 review
   names `oneframe/memory.py` and `oneframe/runtimes.py`. (a) Skip backticked paths in
   `docs/reviews/`, as in specs, and keep checking their links; (b) edit the review.
   *Recommended: (a):* a review is a dated record, and should stay as the owner read it.
4. **Does spec 002's check get its own command,** `npm run bench:fit-check`, with `bench:fit` then
   needing a node? *Recommended: yes:* the check lives with its node; shipped code names neither.
5. **A lasting test on function length or parameter count?** It would be a new rule.
   *Recommended: no:* measure once in the PR, so no cap later bends a design.
