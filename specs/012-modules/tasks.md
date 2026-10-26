# 012: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. **The docs check and the reviews** (question 2), before any module moves. With (a):
  `scripts/check-docs.js` as (a) says, the docs check's line in `AGENTS.md`, and AC6's node test.
  With (b): no code; tasks 5 and 6 edit the review's lines as they move each module.
- [x] 2. **`oneframe/reading.py`:** `text`, `check_id`, `number`, `version_tuple`, `at_least`,
  `at_most`, `read_object` (raising the caller's error class), `ProblemsError` and `discover`.
  `ManifestError` and `DefinitionError` subclass `ProblemsError`; `manifest.py`, `memory.py`,
  `runtimes.py` and `registry.py` use it, and `Precision.runs_on_card` compares with `at_least`.
  AC4's command prints only `reading.py`.
- [x] 3. **Malformed definitions are problems,** if the owner said yes to question 1. Add
  `test_a_malformed_definition_is_a_problem_never_a_crash` (AC3), a case for each in Problem;
  `parse` reads `version`, `title` and `summary` before it decides, refuses a `run`, `licence` or
  `links` that is not an object, a `run` field that is not text and `devices` that is not a list;
  runtime definitions are read through `read_object`.
- [x] 4. **`manifest.parse` in named steps:** one function per part, each adding its problems,
  then one raise. The manifest tests pass unchanged.
- [ ] 5. **The memory package.** `oneframe/memory/` with `model`, `settings`, `estimate`, `fit` and
  `learned` and a docstring-only `__init__.py`; `learned.py` logs as `oneframe.memory`. Callers and
  the tests' imports change; nothing else does. Add
  `test_reading_a_manifest_loads_only_the_memory_model` (AC1), and
  `test_a_package_root_holds_only_its_docstring` with its `memory` and `models` cases (AC7). In the
  same commit, since the docs check reads these paths: the Memory row of `docs/architecture.md`
  names the package, and `docs/roadmap.md` loses its `oneframe/memory.py` line and the manifest's
  import of all of memory.
- [ ] 6. **The runtimes package.** `oneframe/runtimes/` with `definition`, `plan` (`planned_card`),
  `manager` (with `RuntimeSide` and `NoRuntimes`) and `install` (from `runtime_install.py`), and a
  docstring-only `__init__.py`. Callers and the tests' imports change, the install module imported
  as `runtime_install` where tests patch it. Add
  `test_planning_loads_neither_the_manager_nor_the_install` (AC2) and AC7's `runtimes` case. In the
  same commit: the Runtimes row of `docs/architecture.md` names the package, and `docs/roadmap.md`
  loses its `oneframe/runtimes.py` line.
- [ ] 7. **`install` in named steps:** one function per `STEPS` entry over one `_Install`, uv's
  environments built once, sync through `run`; `install`, `_freeze` and `run_step` stay
  module-level. The install tests pass unchanged, the order of `runtime.step` events included.
- [ ] 8. **Docs and measures:** delete from `docs/roadmap.md` the rest of what this spec takes on
  (`install` and `parse` under "Long functions"); the Manifests and Registry rows of
  `docs/architecture.md`. Measure AC5 and run AC4's command for the PR.
- [ ] 9. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC7 one
  by one, with AC5's measure pasted.
