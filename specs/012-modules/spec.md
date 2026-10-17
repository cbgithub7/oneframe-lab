# 012: Modules that are several, and long functions

Status: draft
Owner approval: (date, once approved)

The sixth of eight clean-up specs (007 to 014), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
"Modules that are several" and "Long functions" items. It lands after 008, 010 and 011, which change
`runtimes.py` and `runtime_install.py` first, so each file moves once, as those specs leave it, and
before 013, which edits the moved modules.

## Problem

- `oneframe/memory.py` (1,430 lines) is five modules, and `oneframe/runtimes.py` (1,071) three,
  with the install beside it. Reading a `node.json` loads all of memory and what it imports.
- A malformed `node.json` is accepted or crashes. Accepted: no `version` or `title` (read as `""`
  after the problems check), `devices` as an object, a `run.runtime` or `summary` that is not text.
  A `run`, `licence` or `links` that is not an object, or `devices` as a number, raises what
  `registry.discover` does not catch, and the engine fails to start; a `runtime.json` not in UTF-8
  does the same in `runtimes.discover`.
- Reading a definition is written twice (the text field, the id rule, the error class, the file,
  the discover loop), and versions compare two ways (`memory._version`, `runtimes.version_tuple`).
- `install` is 179 lines with four closures; `manifest.parse` is 118 with two.

## Requirements

1. **Memory is a package,** `oneframe/memory/`: `model`, `settings`, `estimate`, `fit` and
   `learned`. `model` imports only the standard library and `oneframe.reading`; reading a manifest
   loads it alone of the five.
2. **Runtimes is a package,** `oneframe/runtimes/`: `definition`, `plan`, `manager` and `install`
   (today's `runtime_install.py`). Planning loads neither the manager nor the install.
3. **Each of the two packages' roots holds only its docstring,** as `oneframe/models/`'s does:
   callers import its modules, and nothing is re-exported, so a light module stays light.
4. **One way to read a definition.** `oneframe/reading.py`, standard library only, holds the text
   field, the id rule, the number test, the version compare, reading a JSON object from a file, one
   `ProblemsError`, and the discover loop. Manifests, memory models and runtimes use it.
5. **A malformed definition is a problem, never a crash** (open question 1). `parse` reads every
   field before it decides, and a field of the wrong type is a problem naming it.
6. **`parse` and `install` in named steps,** each under 60 lines, behaving as today.
7. **Nothing else a person sees changes:** events, records, `Fit.to_json`, messages and logger
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
  Fails today: some cases are accepted, and the others crash. Only if the owner says yes to
  question 1.
- [ ] AC4: `rg -n 'class (ManifestError|DefinitionError|ProblemsError)\(ValueError\)|re\.compile\(r"\^\[a-z0-9\]\+\(\?:|def (_?text\((data|key)|_?number\(value|_version\(|version_tuple\()|is already used by|should hold one JSON object' engine/src/oneframe`
  prints only lines in `engine/src/oneframe/reading.py`. Today it prints 13, in `manifest.py`,
  `memory.py`, `registry.py` and `runtimes.py`.
- [ ] AC5: No function in `manifest.py` or `runtimes/install.py` reaches 60 lines
  (`end_lineno - lineno + 1` from Python's `ast`, measured once and pasted in the PR). Today
  `install` is 179 and `parse` 118.
- [ ] AC6: The node test "a review may name a path that has since moved, and its links are still
  checked" (`tests/app/guards.test.js`). Fails today: the docs check fails such a review. Only if
  the owner answers (a) to question 2.
- [ ] AC7: `test_a_package_root_holds_only_its_docstring`: the `__init__.py` of `oneframe/memory/`,
  `oneframe/runtimes/` and `oneframe/models/` each hold a docstring and nothing else (Python's
  `ast`), so nothing is re-exported. Fails today: the first two packages do not exist.

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

**Docs that change:** with (a) to question 2, the docs check's line in `AGENTS.md` Commands; in
`docs/architecture.md`, the Manifests, Registry, Memory and Runtimes rows; and `docs/roadmap.md`,
whose memory, runtimes and manifest items go as each module moves (the docs check reads their
paths), and the install and `parse` under "Long functions". With (a), `scripts/check-docs.js`
changes as question 2 says.

**Risks.** `from oneframe.memory import fit` is the module: pyright flags it called as a function,
and ruff a local that shadows an import (`margin = margin(...)`). `runtimes.definition` imports
neither the manager nor the executors, and 010's provider imports `RuntimeSide` under
`TYPE_CHECKING` only, so no cycle forms.

## Tests

- Added:
    - `test_server_and_nodes.py`: `test_reading_a_manifest_loads_only_the_memory_model` and
      `test_a_package_root_holds_only_its_docstring`, a case per package;
    - `test_runtime_plan.py`: `test_planning_loads_neither_the_manager_nor_the_install`;
    - `test_ports_and_manifests.py`: `test_a_malformed_definition_is_a_problem_never_a_crash`;
    - `guards.test.js`: "a review may name a path that has since moved, and its links are still
      checked" (question 2).
- Changed, names kept: the tests' imports of the two packages, the install module imported as
  `runtime_install` so monkeypatch targets hold.
- Moved: none.
- Renamed: none.
- Removed: none.

## Verification

AC4 and AC5 run in the PR. AC1 to AC3, AC7 and the pytests above run in `npm run engine:check`,
and AC6 in `npm run check`, on ubuntu-latest and windows-latest. No step needs a GPU or a local
session: the modules move as they are, and 013's local session runs spec 002's check on a card
through them.

## Decisions taken

- `Target` and `Device` go with the estimate, and `Checkpoints` with the fit: where each is used.
- `learned.py` logs as `oneframe.memory`, so logs and diagnostics read as before.
- `ManifestError` and `DefinitionError` stay, under one `ProblemsError`: callers catch them by name.
- Nodes and runtimes are found by one `discover`: one walk, one problem and duplicate-id rule.
- `pin.find_manifest` keeps its own walk: it must find a manifest the check would refuse.
- One version compare for capabilities, drivers and the torch floor: `"8"` and `"8.0"` now agree.
- `runtimes.plan` (94 lines) and `graph.plan` (100) stay: pure, tested, and not flagged.

## Out of scope

- The scheduler's long functions and parameters, the executor provider, and `RuntimeSide`'s
  design (010); this spec only moves it.
- The test guard (007); processes and environments (008); the node API and `allow_network` (009);
  files, hashes, downloads and the layout's paths, the install's included (011); the tests'
  clean-up (014).
- `bench_fit` naming the `torch` runtime, spec 002's check leaving the package, a test of
  "Nothing hard-coded", and the device, vendor and OS names outside v1 (013).

## Open questions

1. **Is a malformed `node.json` a problem, shown beside the catalogue,** in each case in Problem?
   *Recommended: yes, as bug fixes:* today it is listed untitled or stops the engine, and no node
   in the repo is affected.
2. **How does the docs check treat a review that names a moved module?** The 2026-10-12 review
   names `oneframe/memory.py` and `oneframe/runtimes.py`. (a) Skip backticked paths in
   `docs/reviews/`, as in specs, and keep checking their links; (b) edit the review.
   *Recommended: (a):* a review is a dated record, and should stay as the owner read it.
3. **A lasting test on function length or parameter count?** It would be a new rule.
   *Recommended: no:* measure once in the PR, so no cap later bends a design.
