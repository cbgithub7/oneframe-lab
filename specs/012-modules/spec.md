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
- A malformed `node.json` is accepted or crashes (both checked). Without `version` or `title` it is
  accepted with `""`, since they are read after the problems check. A `run`, `licence` or `links`
  that is not an object, or `devices` that is a number, raises an error `registry.discover` does
  not catch, and the engine fails to start. A `runtime.json` not in UTF-8 stops `runtimes.discover`.
- Reading a definition is written twice (the text field, the id rule, the error class, the discover
  loop), and versions are compared two ways (`memory._version`, `runtimes.version_tuple`).
- `install` is 179 lines with three closures; `manifest.parse` is 118 with two.
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

- [ ] AC1: `test_reading_a_definition_loads_only_what_reading_needs`: in a fresh interpreter,
  importing `oneframe.manifest` loads no other part of memory and none of `files`, `layout` or
  `errors`, and importing `oneframe.runtimes.plan` loads neither `manager` nor `install`. Fails
  today: the manifest loads all of memory and the child script, and there is no plan module.
- [ ] AC2: `test_a_malformed_definition_is_a_problem_never_a_crash`: through both discovers, each
  malformed case (Requirement 5's fields, a `runtime.json` not in UTF-8) is listed as a problem and
  a good sibling loads. Fails today: two cases are accepted, and the others crash.
- [ ] AC3: `rg -n 'class (ManifestError|DefinitionError|ProblemsError)\(ValueError\)|re\.compile\(r"\^\[a-z0-9\]\+\(\?:|def (_text|_version|version_tuple)\(' engine/src/oneframe`
  prints only lines in `engine/src/oneframe/reading.py`. Today it prints seven, in `manifest.py`,
  `memory.py` and `runtimes.py`.
- [ ] AC4: `test_no_engine_module_names_a_runtime_a_node_or_the_tests` (`test_rules.py`): no engine
  module passes a literal runtime id to the runtimes' methods, holds a repo node's id outside a
  docstring, or builds a path through `tests`. Fails today on `bench_fit.py`.
- [ ] AC5: `npm run bench:fit-check -- --data <tmp> --runtimes <empty>` exits 0, writes a report
  saying "Not measured", and leaves TMP empty (the extended `tool.test.js` test "with TMP, TEMP and
  TMPDIR pointed at an empty folder, each tool leaves nothing there"). Fails today: no command.
- [ ] AC6: No function in `manifest.py` or `runtimes/install.py` reaches 60 lines
  (`end_lineno - lineno + 1` from Python's `ast`, measured once and pasted in the PR). Today
  `install` is 179 and `parse` 118.
- [ ] AC7: `test_names_outside_v1_are_refused` (`test_rules.py`): a manifest whose devices name
  `mps`, `xpu` or `rocm`, and a runtime whose build names the vendor `amd` or `intel` or the OS
  `macos`, are each refused with a problem naming the value. Fails today: all are accepted. Only
  if the owner says yes to question 1.
- [ ] AC8: The node test "a review may name a path that has since moved, and its links are still
  checked" (`tests/app/guards.test.js`). Fails today: the docs check fails such a review. Only if
  the owner answers (a) to question 3.

## Design

**The packages,** moved as they are. Memory: `model` (the model and its parser), `settings` (the
settings and margins), `estimate` (`Target`, `Device`, `Learned`, `settings_hash` and the estimate),
`fit` (`Checkpoints` and the fit) and `learned` (the store). Runtimes: `definition`, `plan` (`_card`
becomes `planned_card`, which the manager asks too), `manager` (with 010's `RuntimeSide` and
`NoRuntimes`) and `install`.

**`oneframe/reading.py`**: `text`, `check_id`, `number` (memory's `_number`), `version_tuple`,
`at_least` and `at_most` (which `Precision.runs_on_card` uses too), and `read_object(path)`: the
object and its bytes, or `ProblemsError(source, problems)` for a file that cannot be read, decoded
or parsed, or holds no object. `ManifestError` and `DefinitionError` subclass `ProblemsError`.
`discover(roots, filename, load)` serves `registry` and `runtimes.definition`.

**`manifest.parse`** calls one function per part (the head, ports, params, run, devices, memory and
files, licence and links), each adding its problems, then raises once. **`install`** keeps its
signature. Each `STEPS` entry becomes a function of one `_Install`, which holds the runtime, the
build, the paths, uv's environments (built once) and the step, log and run helpers that are
closures today. Sync goes through `run`, with today's message. `_freeze` and `run_step` stay
module-level, since tests patch them.

**`bench_fit`** keeps measuring a named node (`parse_sets`, `measure`, `make_scheduler`,
`bench_node`, its report), and its `node` argument becomes required. **`engine/tests/fit_check.py`**
takes the AC8 scenario: `Ac8`, `find_ac8`, `three_settings`, the holder, the overrun, `cap_refusal`
and their report. It finds `hardware/` beside itself, takes its runtime from `test.vram`'s
`run.runtime`, and probes through 010's `run_probe`. **`scripts/tool.js`** runs an argument ending
in `.py` as a repo file, by its absolute path, and anything else as a module, as today.

**The rule's test** walks each engine module's AST; mechanisms and exceptions (docstrings, library
names such as `torch` that are not runtime ids) live in the test.

**Names outside v1** (question 1): `VENDORS`, `OSES` and the manifest's devices narrow; the fit's
path for devices it "does not read yet" and the plan's other-vendor refusal go. `hardware.os_name`
still says `macos`: it reports the machine, and the plan refuses it.

**Docs that change:** `AGENTS.md` Commands (`bench:fit` needs a node; a `bench:fit-check` line; the
tools that start through `tool.js`; with (a) to question 3, the docs check's line);
`docs/architecture.md` (the Manifests, Memory, Runtimes and Benches rows); `docs/nodes.md` (the
`bench:fit` section, and the devices a manifest may name); `docs/runtimes.md` (vendors and OSes);
`docs/roadmap.md` (what this spec takes on). With (a), `scripts/check-docs.js` skips backticked
paths in `docs/reviews/`, as in specs, and still checks their links.

**Risks:**

- `from oneframe.memory import fit` is the module: pyright flags it called as a function, and ruff
  a local that shadows an import (`margin = margin(...)`).
- `runtimes.definition` imports neither the manager nor the executors; 010's provider imports
  `RuntimeSide` under `TYPE_CHECKING` only, so no cycle forms.

## Tests

- Added:
    - `test_server_and_nodes.py`: `test_reading_a_definition_loads_only_what_reading_needs`, one
      case for the manifest and one for the plan;
    - `test_ports_and_manifests.py`: `test_a_malformed_definition_is_a_problem_never_a_crash`;
    - `test_rules.py`: `test_no_engine_module_names_a_runtime_a_node_or_the_tests`, and
      `test_names_outside_v1_are_refused` (question 1);
    - `tests/app/guards.test.js`: "a review may name a path that has since moved, and its links are
      still checked" (question 3);
    - `tests/app/tool.test.js`: "a tool given as a .py path runs that file, by its absolute path".
- Moved, names kept: the five tests of the AC8 scenario, from `test_bench_fit.py` to
  `test_fit_check.py`.
- Changed, names kept:
    - the tests' imports of the two packages, the install module imported as `runtime_install` so
      monkeypatch targets hold;
    - in `tool.test.js`, the npm-script list takes the fit check's path, and the TMP test runs it;
    - with question 1, `test_every_problem_in_a_definition_is_listed_at_once` expects the narrowed
      lists.
- Renamed: none.
- Removed: `test_devices_the_engine_does_not_read_yet_are_named`, only if the owner says yes to
  question 1: the path it tests is gone, and AC7's test checks the refusal that replaces it.

## Verification

AC3 and AC6 run in the PR, with AC6's report pasted there. AC1, AC2, AC4, AC7 and the pytests above
run in `npm run engine:check`, and AC5 and AC8 in `npm run check`, on ubuntu-latest and
windows-latest. No step needs a GPU. The AC8 scenario's card path is moved, not run: the PR says
so, and the owner may run `npm run bench:fit-check` in a local session to see it on a card.

## Decisions taken

- `Target` and `Device` go with the estimate, and `Checkpoints` with the fit: where each is used.
- `learned.py` logs as `oneframe.memory`, so logs and diagnostics read as before.
- `ManifestError` and `DefinitionError` stay, under one `ProblemsError`: callers catch them by name.
- Nodes and runtimes are found by one `discover`: one walk, one problem and duplicate-id rule.
- One version compare for capabilities, drivers and the torch floor: `"8"` and `"8.0"` now agree.
- `fit_check.py` lives in `engine/tests/`, not in `hardware/`, which ruff and pyright treat as 3.11.
- `tool.js` takes a `.py` path besides a module, rather than the tests becoming a package.
- `runtimes.plan` (94 lines) and `graph.plan` (100) stay: pure, tested, and not flagged.

## Out of scope

- The scheduler's long functions and parameters, the executor provider, and `RuntimeSide`'s
  design (010); this spec only moves it.
- Processes and environments (008); the node API and `allow_network` (009); files, hashes,
  downloads and the layout's paths, the install's included (011); the tests' clean-up (013).
- "Nothing hard-coded" in JavaScript: the app names no node today.
- macOS, AMD and Intel, which the platform matrix leaves out of v1.

## Open questions

1. **Are names outside v1 refused?** Manifests then name only `cpu` and `cuda`, and builds only
   `nvidia` or `none`, on `windows` or `linux`. *Recommended: yes:* nothing uses them, they are
   outside the platform matrix, and widening later still reads every file written now.
2. **Is a malformed `node.json` a problem, rather than accepted or a crash?** One without `version`
   or `title`, or with `run`, `licence`, `links` or `devices` of the wrong type, then shows beside
   the catalogue as a problem. *Recommended: yes, as bug fixes:* today it is listed untitled or
   stops the engine, and no node in the repo is affected.
3. **How does the docs check treat a review that names a moved module?** The 2026-10-12 review
   names `oneframe/memory.py` and `oneframe/runtimes.py`. (a) Skip backticked paths in
   `docs/reviews/`, as in specs, and keep checking their links; (b) edit the review.
   *Recommended: (a):* a review is a dated record, and should stay as the owner read it.
4. **Does spec 002's check get its own command,** `npm run bench:fit-check`, with `bench:fit` then
   needing a node? *Recommended: yes:* the check lives with its node; shipped code names neither.
5. **A lasting test on function length or parameter count?** It would be a new rule.
   *Recommended: no:* measure once in the PR, so no cap later bends a design.
