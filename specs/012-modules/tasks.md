# 012: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [ ] 1. **The docs check and the reviews** (question 3), before any module moves. With (a):
  `scripts/check-docs.js` skips backticked paths in `docs/reviews/` and still checks their links;
  the docs check's line in `AGENTS.md` says so; add the node test in AC8. With (b): no code; tasks 5
  and 6 edit the review's lines as they move each module.
- [ ] 2. **`oneframe/reading.py`:** `text`, `check_id`, `number`, `version_tuple`, `at_least`,
  `at_most`, `read_object`, `ProblemsError` and `discover`. `ManifestError` and `DefinitionError`
  subclass `ProblemsError`; `manifest.py`, `memory.py`, `runtimes.py` and `registry.py` use it, and
  `Precision.runs_on_card` compares with `at_least`. AC3's command prints only `reading.py`.
- [ ] 3. **Malformed definitions are problems.** Add
  `test_a_malformed_definition_is_a_problem_never_a_crash` (AC2); `parse` reads `version`, `title`
  and `summary` before it decides, refuses a `run`, `licence` or `links` that is not an object and
  `devices` that is not a list; runtime definitions are read through `read_object`.
- [ ] 4. **`manifest.parse` in named steps:** one function per part, each adding its problems,
  then one raise. The manifest tests pass unchanged.
- [ ] 5. **The memory package.** `oneframe/memory/` with `model`, `settings`, `estimate`, `fit` and
  `learned` and a docstring-only `__init__.py`; `learned.py` logs as `oneframe.memory`. Callers and
  the tests' imports change; nothing else does. Add
  `test_reading_a_definition_loads_only_what_reading_needs` with its manifest case (AC1). The Memory
  row of `docs/architecture.md` names the package.
- [ ] 6. **The runtimes package.** `oneframe/runtimes/` with `definition`, `plan` (`planned_card`),
  `manager` (with `RuntimeSide` and `NoRuntimes`) and `install` (from `runtime_install.py`), and a
  docstring-only `__init__.py`. Callers and the tests' imports change, the install module imported
  as `runtime_install` where tests patch it. Add AC1's plan case. The Runtimes row of
  `docs/architecture.md` names the package.
- [ ] 7. **`install` in named steps:** one function per `STEPS` entry over one `_Install`, uv's
  environments built once, sync through `run`; `_freeze` and `run_step` stay module-level. The
  install tests pass unchanged, the order of `runtime.step` events included.
- [ ] 8. **`scripts/tool.js` runs a `.py` repo path** by its absolute path, as well as a module; add
  "a tool given as a .py path runs that file, by its absolute path".
- [ ] 9. **Spec 002's check leaves the package** (question 4). `engine/tests/fit_check.py` takes the
  AC8 scenario, finds `hardware/` beside itself, and reads its runtime from `test.vram`'s manifest;
  `bench_fit`'s `node` becomes required. Add `bench:fit-check` to `package.json`; in `tool.test.js`,
  the npm-script list takes it and the TMP test runs it with an empty `--runtimes` (AC5). Move the
  five scenario tests to `test_fit_check.py`, names kept. Update `AGENTS.md` Commands, the
  `bench:fit` section of `docs/nodes.md`, the Benches row of `docs/architecture.md`, and the summary
  in `test.vram`'s `node.json`.
- [ ] 10. **The rule's test:** add `test_no_engine_module_names_a_runtime_a_node_or_the_tests` in
  `engine/tests/test_rules.py` (AC4).
- [ ] 11. **Names outside v1** (only if the owner said yes to question 1): narrow `VENDORS`, `OSES`
  and the manifest's devices; remove the fit's path for unread devices and the plan's other-vendor
  refusal; add `test_names_outside_v1_are_refused` (AC7); remove
  `test_devices_the_engine_does_not_read_yet_are_named`, as Tests lists; update the expected
  problems of `test_every_problem_in_a_definition_is_listed_at_once`; `docs/runtimes.md`'s vendors
  and OSes, and the devices in `docs/nodes.md`.
- [ ] 12. **Docs and measures:** delete from `docs/roadmap.md` what this spec takes on (the modules,
  "Nothing hard-coded", `install` and `parse`, the device, vendor and OS names). Measure AC6 and run
  AC3's command for the PR.
- [ ] 13. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC8 one
  by one, with AC6's measure pasted and the AC8 scenario's card path named as unverified.
