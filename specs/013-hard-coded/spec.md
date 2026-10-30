# 013: Nothing hard-coded, and v1's names

Status: in progress (tasks 1 to 6 done; in review in [PR #49](https://github.com/cbgithub7/oneframe-lab/pull/49))
Owner approval: 2026-10-18, every open question answered as recommended
Changed after approval (from `/code-review`, 2026-10-18), approved by the owner 2026-10-23: the rule
  test's exceptions are keyed by where a name stands, never by file alone.

The seventh of eight clean-up specs (007 to 014), from section 3 of the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) and the [roadmap](../../docs/roadmap.md)'s
"Nothing hard-coded" and "Dead weight" items (review of 2026-10-02, 4.10). It lands after 012, so
`fit_check.py` imports the new packages and the narrowing edits each module once, where 012 leaves
it; and it needs 010's `run_probe`.

## Problem

- AGENTS.md says no code outside a node's folder names a node, a model or a pipeline, and no test
  holds it. `bench_fit`, shipped in the package, names the runtime `torch` and reaches into
  `engine/tests/hardware` for spec 002's AC8 scenario.
- Manifests accept the devices `mps`, `xpu` and `rocm`, and `runtime.json` the vendors `amd` and
  `intel` and the OS `macos`, all outside v1's platforms. Memory keeps paths for devices it does
  not read yet, and the plan a refusal for builds of other vendors.

## Requirements

1. **Nothing in the engine names a runtime, a node or the tests.** `bench_fit` measures the node a
   person names, as today, and needs one.
2. **Spec 002's check lives with its node:** `engine/tests/fit_check.py`, run by
   `npm run bench:fit-check` (open question 2), takes its runtime from its node's manifest, and
   reports as today.
3. **A test holds the rule** for every engine module, with each exception named in the test.
4. **Names outside v1 are refused** (open question 1): a manifest's devices are `cpu` and `cuda`;
   a build's vendor is `nvidia` or `none`, and its OS `windows` or `linux`.

## Acceptance criteria

- [ ] AC1: `test_no_engine_module_names_a_runtime_a_node_or_the_tests` (`test_rules.py`): no engine
  module holds, outside a docstring, a repo runtime's or node's id (but for library names the test
  lists by where each stands), or `tests` as a path part. Fails today on `bench_fit.py`.
- [ ] AC2: The node test "bench:fit-check runs spec 002's check from the tests folder"
  (`tests/app/tool.test.js`): `package.json`'s `bench:fit-check`, run with `--data <tmp>
  --runtimes <empty>`, exits 0 with a report that says "Not measured". Fails today: no such script.
- [ ] AC3: The node test "a tool given as a .py path runs that file, by its absolute path"
  (`tests/app/tool.test.js`). Fails today: `scripts/tool.js` runs every tool as a module.
- [ ] AC4: `test_names_outside_v1_are_refused` (`test_rules.py`): the devices `mps`, `xpu` and `rocm`
  in a manifest, and the vendors `amd` and `intel` and the OS `macos` in a build, are each refused
  with a problem. Fails today: all are accepted. Only if the owner says yes to question 1.

## Design

**`bench_fit`** keeps measuring a named node (`parse_sets`, `measure`, `make_scheduler`,
`bench_node`, its report), and its `node` argument becomes required; its docstring loses the
scenario. **`engine/tests/fit_check.py`** takes spec 002's scenario: `Ac8`, `find_ac8`,
`three_settings`, the holder, the overrun, `cap_refusal` and their report. It finds `hardware/`
beside itself, takes its runtime from its node's manifest, and probes through 010's `run_probe`.

**`scripts/tool.js`** runs an argument ending in `.py` as a repo file, by its absolute path, and
anything else as a module, as today, so `bench:fit-check` starts through it as every tool that opens
a data root does, with uv's temporary files under that root.

**The rule's test** walks each engine module's AST; its exceptions (docstrings, library names such
as `torch` that are not runtime ids) live in the test, each keyed by where the name stands (such
as an import, a `sys.modules` key, a cache folder's name or a check of a lock's package names),
never by file alone: a listed file that gains a runtime id is still caught.

**Names outside v1** (question 1): `VENDORS`, `OSES` and the manifest's devices narrow; memory's
paths for devices it "does not read yet" (the parser's and the fit's) and the plan's other-vendor
refusal go. `hardware.os_name` still says `macos`: it reports the machine as it is.

**Docs that change:** `AGENTS.md` Commands (`bench:fit` needs a node; a `bench:fit-check` line; the
tools that start through `tool.js`); the Benches row of `docs/architecture.md`; `docs/nodes.md`'s
`bench:fit` section and devices; `docs/runtimes.md`'s vendors and OSes; and `docs/roadmap.md`, which
loses "Nothing hard-coded" and the device, vendor and OS names. `test.vram`'s `node.json` summary
and `ac8.json` comment name the new command.

**Risks.** The moved scenario's card path runs in no CI job; the local session in Verification
runs it once. A library named like a runtime (`torch`) is allowed only where the test lists it, so
a new use fails the test until a person adds it there, which a review sees.

## Tests

- Added:
    - `test_rules.py`: `test_no_engine_module_names_a_runtime_a_node_or_the_tests`, and
      `test_names_outside_v1_are_refused` (question 1);
    - `tool.test.js`: "bench:fit-check runs spec 002's check from the tests folder", and "a tool
      given as a .py path runs that file, by its absolute path".
- Moved, names kept, from `test_bench_fit.py` to `test_fit_check.py`, the five tests of spec 002's
  scenario: `test_the_holder_leaves_room_for_the_first_change_only_on_every_card`,
  `test_the_report_says_when_a_step_did_not_happen_as_designed`,
  `test_ac8_is_described_by_its_node_folder_and_sized_from_the_nodes_own_model`,
  `test_the_report_says_what_was_not_measured` and
  `test_torchs_message_shows_whether_the_cap_refused_a_request_the_card_had_room_for`.
- Changed, names kept:
    - in `tool.test.js`, the npm-script list takes the fit check's path, and the TMP test runs it;
    - with question 1, `test_every_problem_in_a_definition_is_listed_at_once` expects the narrowed
      lists.
- Renamed: none.
- Removed: `test_devices_the_engine_does_not_read_yet_are_named`, only if the owner says yes to
  question 1: the path it tests is gone, and AC4's test checks the refusal that replaces it.

## Verification

AC1, AC4 and the pytests above run in `npm run engine:check`, and AC2 and AC3 in `npm run check`,
on ubuntu-latest and windows-latest. After the merge, a local session (`/local-session`) on an
NVIDIA card runs `npm run bench:fit-check` once; its report, naming the machine, goes to
`specs/013-hard-coded/reports/<date>-local.md`. It also runs spec 002's fit through 012's packages
on a card.

## Decisions taken

- `fit_check.py` lives in `engine/tests/`, not in `hardware/`, which ruff and pyright treat as 3.11.
- `tool.js` takes a `.py` path besides a module, rather than the tests becoming a package.
- The rule flags a string that is an id, or has one or `tests` as a path part, and an import:
  prose that names a library (a message saying torch saw no card) names no runtime.
- `test_rules.py` also has `test_a_listed_module_that_gains_a_runtime_id_is_still_caught`, so the
  Design's keying by place is itself tested.
- In `test_every_problem_in_a_definition_is_listed_at_once`, the build with a malformed capability
  becomes `cu118` on `nvidia`, so its capability is still checked now that `amd` stops it first.

## Out of scope

- The memory and runtimes packages, `reading.py`, malformed definitions, and the long functions of
  the install and the parser (012).
- The test guard (007); processes and environments (008); the node API and `allow_network` (009);
  `run_probe` and the executors (010); files and hashes (011); the tests' clean-up (014).
- "Nothing hard-coded" in JavaScript: the app names no node today.
- macOS, AMD and Intel, which the platform matrix leaves out of v1.

## Open questions (answered by the owner, 2026-10-18)

1. **Are names outside v1 refused?** Manifests then name only `cpu` and `cuda`, and builds only
   `nvidia` or `none`, on `windows` or `linux`. *Answered: yes:* nothing uses them, they are
   outside the platform matrix, and widening later still reads every file written now.
2. **Does spec 002's check get its own command,** `npm run bench:fit-check`, with `bench:fit` then
   needing a node? *Answered: yes:* the check lives with its node; shipped code names neither.
