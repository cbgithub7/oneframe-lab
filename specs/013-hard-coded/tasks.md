# 013: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves, renames or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. **`scripts/tool.js` runs a `.py` repo path** by its absolute path, as well as a module, and
  its usage comment says so; add "a tool given as a .py path runs that file, by its absolute path"
  (AC3).
- [x] 2. **Spec 002's check leaves the package** (question 2): `engine/tests/fit_check.py` takes
  the scenario, as Design says; `bench_fit`'s `node` becomes required, and its docstring loses the
  scenario. Add `bench:fit-check` to `package.json`; in `tool.test.js`, the npm-script list takes
  it, the TMP test runs it, and add "bench:fit-check runs spec 002's check from the tests folder"
  (AC2). Move the five scenario tests to `test_fit_check.py`, names kept. Update `AGENTS.md`
  Commands, the `bench:fit` section of `docs/nodes.md`, the Benches row of `docs/architecture.md`,
  and the command named in `test.vram`'s `node.json` summary and `ac8.json` comment.
- [x] 3. **The rule's test:** add `test_no_engine_module_names_a_runtime_a_node_or_the_tests` in
  `engine/tests/test_rules.py` (AC1).
- [x] 4. **Names outside v1** (only if the owner said yes to question 1): narrow `VENDORS`, `OSES`
  and the manifest's devices; remove memory's paths for unread devices (the parser's and the fit's)
  and the plan's other-vendor refusal; add `test_names_outside_v1_are_refused` (AC4); remove
  `test_devices_the_engine_does_not_read_yet_are_named`, as Tests lists; update the expected
  problems of `test_every_problem_in_a_definition_is_listed_at_once`; `docs/runtimes.md`'s vendors
  and OSes, and the devices in `docs/nodes.md`.
- [x] 5. **The roadmap:** delete from `docs/roadmap.md` what this spec takes on ("Nothing
  hard-coded", and the device, vendor and OS names under "Dead weight").
- [x] 6. `/code-review` on the branch and fix what it finds; open the PR, checking AC1 to AC4 one
  by one.
- [ ] 7. After the merge, the local session in Verification on a machine with an NVIDIA card, and
  its report under `specs/013-hard-coded/reports/`.
