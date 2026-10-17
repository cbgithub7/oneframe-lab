# 012: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [ ] 1. **The docs check and the reviews** (question 3), before any module moves. With (a):
  `scripts/check-docs.js` skips backticked paths in `docs/reviews/` and still checks their links;
  the docs check's line in `AGENTS.md` says so; add the node test in AC6. With (b): no code; tasks 5
  and 6 edit the review's lines as they move each module.
- [ ] 2. **`oneframe/reading.py`:** `text`, `check_id`, `number`, `version_tuple`, `at_least`,
  `at_most`, `read_object` (raising the caller's error class), `ProblemsError` and `discover`.
  `ManifestError` and `DefinitionError` subclass `ProblemsError`; `manifest.py`, `memory.py`,
  `runtimes.py` and `registry.py` use it, and `Precision.runs_on_card` compares with `at_least`.
  AC4's command prints only `reading.py`.
- [ ] 3. **Malformed definitions are problems.** Add
  `test_a_malformed_definition_is_a_problem_never_a_crash` (AC3), a case for each in Problem;
  `parse` reads `version`, `title` and `summary` before it decides, refuses a `run`, `licence` or
  `links` that is not an object, a `run` field that is not text and `devices` that is not a list;
  runtime definitions are read through `read_object`.
- [ ] 4. **`manifest.parse` in named steps:** one function per part, each adding its problems,
  then one raise. The manifest tests pass unchanged.
- [ ] 5. **The memory package.** `oneframe/memory/` with `model`, `settings`, `estimate`, `fit` and
  `learned` and a docstring-only `__init__.py`; `learned.py` logs as `oneframe.memory`. Callers and
  the tests' imports change; nothing else does. Add
  `test_reading_a_manifest_loads_only_the_memory_model` (AC1). In the same commit, since the docs
  check reads these paths: the Memory row of `docs/architecture.md` names the package, and
  `docs/roadmap.md` loses its `oneframe/memory.py` line and the manifest's import of all of memory.
- [ ] 6. **The runtimes package.** `oneframe/runtimes/` with `definition`, `plan` (`planned_card`),
  `manager` (with `RuntimeSide` and `NoRuntimes`) and `install` (from `runtime_install.py`), and a
  docstring-only `__init__.py`. Callers and the tests' imports change, the install module imported
  as `runtime_install` where tests patch it. Add
  `test_planning_loads_neither_the_manager_nor_the_install` (AC2). In the same commit: the Runtimes
  row of `docs/architecture.md` names the package, and `docs/roadmap.md` loses its
  `oneframe/runtimes.py` line.
- [ ] 7. **`install` in named steps:** one function per `STEPS` entry over one `_Install`, uv's
  environments built once, sync through `run`; `install`, `_freeze` and `run_step` stay
  module-level. The install tests pass unchanged, the order of `runtime.step` events included.
- [ ] 8. **`scripts/tool.js` runs a `.py` repo path** by its absolute path, as well as a module, and
  its usage comment says so; add "a tool given as a .py path runs that file, by its absolute path".
- [ ] 9. **Spec 002's check leaves the package** (question 4). `engine/tests/fit_check.py` takes
  the scenario, finds `hardware/` beside itself, and reads its runtime from its node's manifest;
  `bench_fit`'s `node` becomes required, and its docstring loses the scenario. Add
  `bench:fit-check` to `package.json`; in `tool.test.js`, the npm-script list takes it, the TMP
  test runs it, and add "bench:fit-check runs spec 002's check from the tests folder" (AC8). Move
  the five scenario tests to `test_fit_check.py`, names kept. Update `AGENTS.md` Commands, the
  `bench:fit` section of `docs/nodes.md`, the Benches row of `docs/architecture.md`, and the
  command named in `test.vram`'s `node.json` summary and `ac8.json` comment.
- [ ] 10. **The rule's test:** add `test_no_engine_module_names_a_runtime_a_node_or_the_tests` in
  `engine/tests/test_rules.py` (AC7).
- [ ] 11. **Names outside v1** (only if the owner said yes to question 1): narrow `VENDORS`, `OSES`
  and the manifest's devices; remove memory's paths for unread devices (the parser's and the fit's)
  and the plan's other-vendor refusal; add `test_names_outside_v1_are_refused` (AC9); remove
  `test_devices_the_engine_does_not_read_yet_are_named`, as Tests lists; update the expected
  problems of `test_every_problem_in_a_definition_is_listed_at_once`; `docs/runtimes.md`'s vendors
  and OSes, and the devices in `docs/nodes.md`.
- [ ] 12. **Docs and measures:** delete from `docs/roadmap.md` the rest of what this spec takes on
  ("Nothing hard-coded", `install` and `parse`, the device, vendor and OS names); the Manifests and
  Registry rows of `docs/architecture.md`. Measure AC5 and run AC4's command for the PR.
- [ ] 13. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC9 one
  by one, with AC5's measure pasted.
- [ ] 14. After the merge, the local session in Verification on a machine with an NVIDIA card, and
  its report under `specs/012-modules/reports/`.
