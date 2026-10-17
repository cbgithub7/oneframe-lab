# 007: The test guard sees every test

Status: draft
Owner approval: (date, once approved)

From the [review of 2026-10-12](../../docs/reviews/2026-10-12.md) (section 4) and the
[roadmap](../../docs/roadmap.md)'s clean-up list. The first of the clean-up specs 007 to 014, so
that the refactors after it are guarded and can rename tests honestly.

## Problem

`scripts/test-guard.js` reads test names from the source: `def test_*` in Python, `test("...")` in
JavaScript. It sees 431 Python names, while pytest collects 570 engine test IDs, 161 of them
cases of 22 parametrized functions. Deleting a case, adding a skip mark or emptying a body all pass
it. A test can be renamed only by listing its old name as removed; nothing then checks that the new
name exists, and the line allows that name to vanish for good, since every spec ever written is
read.

## Requirements

1. **Every case is seen.** The guard compares the engine's collected test IDs, parametrized cases
   included, with those at the merge base. An ID that is gone, with no line in a spec, fails it.
   Tests match by name and case, not by file, so moving a test to another file stays free; a name
   defined in two files counts twice.
2. **A skip or an emptied body counts as a removal.** For each ID, the listing records:
    - its skips: a skip, skipif or xfail mark on its function, case, class or module, and a
      `pytest.skip`, `pytest.xfail` or `pytest.importorskip` call in its body;
    - whether its body is empty: after the docstring, nothing but `pass`, `...` and constants, or a
      `return` before anything else.

   An existing test that gains a skip, or whose body becomes empty, needs a "- Removed" line, as a
   removal does. JavaScript tests, still read from source, carry the same two flags: a `skip` or
   `todo` option or modifier, or a `t.skip()` or `t.todo()` call; and an empty body.
3. **Case IDs are names.** Collection refuses a case whose ID pytest made from its position
   (`memory0`, `exc3`), naming the test: otherwise removing a middle case and adding one at the end
   leaves the set of IDs as it was. The functions with such IDs today are given explicit `ids`.
4. **Renames.** A spec's Tests section may hold lines ``- Renamed: `old` -> `new` ``. The old test
   may go while the new one exists; a new name that matches no test fails the guard. A name is a
   function, which covers its cases, or one case. The new test is compared with the old one's
   flags, so a rename that gains a skip or an empty body still needs a "- Removed" line.
5. **Only live specs allow.** "- Removed" and "- Renamed" lines count only in a spec that is not
   done or dropped, or that changed since the merge base.
6. **The base comes from git.** `engine/tests/collected.txt` is the listing, committed: sorted, one
   ID per line with its flags. The guard reads it at the merge base with `git show`, so the base is
   never collected.
    - The guard fails when the list in the checkout differs from the live collection, and names
      `node scripts/test-guard.js --write`, which rewrites it. `--write` cannot hide a removal,
      since the base side comes from git.
    - A base without the list (a branch from before this spec) is compared by source names, as
      today.
7. **No quiet pass.** The guard now needs the engine environment. Without it, or when collection
   fails, it fails and says why. Its header says what it still cannot see (Design).

## Acceptance criteria

- [ ] AC1: In a throwaway git repository whose branch drops one case of a parametrized test and
  rewrites the list with `--write`, the guard exits 1 naming that ID (node test "a parametrized case
  that is gone fails, naming its ID, even after --write" in `tests/app/guards.test.js`, with a
  stand-in collection). Today's guard lets it through.
- [ ] AC2: An ID that gains a flag, a skip or an empty body, under its own name or a name a
  "- Renamed" line gave it, is reported as removed unless a spec lists it (node test "a test that
  gains a skip or an empty body counts as removed"). Today's guard lets it through.
- [ ] AC3: The listing of a made-up suite equals its expected text. The suite holds a test skipped
  by a mark at each level, one by each call, one emptied in each way, and one that asserts (pytester
  test `test_the_listing_of_a_made_up_suite_is_as_expected` in `engine/tests/test_listing.py`).
- [ ] AC4: The JavaScript entries of a made-up file equal their expected list, with each form of
  skip and of empty body flagged, and a `test(` inside a string not counted (node test "JavaScript
  tests are read with their skips and empty bodies").
- [ ] AC5: Collecting a made-up suite with a case numbered by its position fails, naming the test
  (pytester test `test_a_case_numbered_by_its_position_is_refused`). Today's suite has 57 such
  cases, in seven functions.
- [ ] AC6: A "- Renamed" line lets the old name go only while the new one exists: with the new name
  present the guard passes, and without it the guard names it (node test "a rename lets the old
  name go only to a name that exists"). Today's guard ignores the line.
- [ ] AC7: A "- Removed" line allows a removal only from a spec not done or dropped, or changed
  since the merge base: the same line in a done spec unchanged on the branch allows nothing (node
  test "only specs being worked on, or changed on the branch, allow a removal"). Today a done spec
  allows the name forever.
- [ ] AC8: When `engine/tests/collected.txt` in the checkout differs from the live collection, the
  guard exits 1 and names `node scripts/test-guard.js --write` (node test "a committed list that
  differs from the collection fails, naming --write", with a stand-in collection).

## Design

**`scripts/test-guard.js`** keeps its exports, so its three existing tests stand unchanged. It
gains:

- `collectPython()`: runs pytest's collection in the engine environment
  (`uv run --project engine --frozen pytest engine --collect-only`) with the listing switched on,
  and reads the listing from pytest's output; a failed collection fails the guard with pytest's
  own message. It takes a second or two on Linux; the PR measures Windows.
- `jsEntries(file, text)`: JavaScript tests from source, with their flags. It reads `test(` only
  where a statement starts; today a string in `tests/app/guards.test.js` counts as a test.
- `allowances(...)`: the Removed and Renamed lines of the specs that count. Whether a spec is done
  or dropped is read by one function in `scripts/check-docs.js`, taken out of the length check that
  reads `Status:` inline today, so the two checks agree. The template is skipped.
- `problems(before, after, allow)`: gone IDs, gained flags, and renames to nothing.
- `--write`, which rewrites the committed list from the live collection.

Its header lists what it cannot see: a skipif whose condition is widened, since only mark names are
recorded; a skip inside a fixture or a helper the test calls; and a gutted assertion such as
`assert True`, which is not an empty body. Reviewers still read a rewritten test beside the old one.

**`engine/tests/fixtures/listing.py`** holds two collection hooks, which `engine/tests/conftest.py`
imports: the listing, switched on only by the guard, and the refusal of positional IDs. The conftest
also enables pytester, for their tests.

**Explicit `ids`** go to the seven functions the refusal names today (in test_child.py,
test_memory.py, test_model_runs.py and test_scheduler.py), and to
`test_failures_are_classified_and_leave_nothing_cached`, whose IDs are the code it runs. Bodies do
not change.

**CI and the Stop hook** need no change: both run the guard after the engine environment exists.

**Docs that change with the work:**

- `AGENTS.md`: rule 6, in the owner's wording (open question 1), and the guard's line under
  Commands, which gains `--write`;
- [specs/README.md](../README.md): which specs' lines count, and the Renamed line;
- `specs/_template/spec.md`: a "- Renamed: none" line under Tests;
- `.github/pull_request_template.md`: "Tests removed" also covers skipped and renamed tests;
- `docs/roadmap.md`: the test guard's item leaves the clean-up list.

**Risks:**

- Collection imports every test module, so a module-level side effect would run on each guard call.
  None exists today.
- A case named by its values (`[6.1-560.94-cu126]`) changes ID when a value changes: that is a
  rename, and needs a line in a spec.
- Two branches that add tests can conflict in `collected.txt`; after merging, `--write` resolves it.
- `main` has no branch protection. A direct push without `--write` leaves the list stale, and
  main's own CI run fails until it is rewritten.

## Tests

- Added: in `tests/app/guards.test.js`, the node tests named in AC1, AC2, AC4, AC6, AC7 and AC8,
  "a base without a committed list is compared by source names" and "a collection that fails
  fails the guard, saying why" (requirement 7); in `engine/tests/test_listing.py`, the pytester
  tests named in AC3 and AC5.
- Removed: none.
- Renamed: none. The case IDs of the eight functions in Design change once, before the list
  exists, when the guard still sees function names only.

## Verification

All eight criteria are tests, run in CI on windows-latest and ubuntu-latest: the node tests in
`npm run check`, the pytester tests in `npm run engine:check`. CI green on both, with one committed
list, shows that collection is the same on both systems.

Once in the PR, with the list committed, against the new guard: delete one case of
`test_unsafe_paths`, add `@pytest.mark.skip` to one test, and run `--write`. Then
`node scripts/test-guard.js --base HEAD` (main has no list yet, so the default base would fall back
to source names) exits 1 naming each; both are then reverted. The PR records the guard's time on
Windows. No hardware steps.

## Decisions taken

- The base is the committed list, read with `git show`: collecting the base needs its environment.
- The list is sorted text, an ID and its flags per line, in LF: it reads the same on Windows.
- The listing is read from pytest's output, so the guard writes nothing to temp (the data root rule).
- The hooks live in a module the conftest imports, so the pytester tests load the same code.
- Skips are compared by mark and call names, not conditions, which differ from system to system.
- Positional IDs are refused at every collection, not only by the guard, so they fail earliest.
- The code-text IDs get names too: any edit to that code would otherwise read as a rename.
- Only specs being worked on, or changed on the branch, allow: otherwise a line allows forever.
- With no base ref the guard still skips, as today; without the engine environment it fails.

## Out of scope

- The footprint test, shared test helpers, the killed-install test, wall-clock bounds and the two
  tests of implementation details, with the rename of `test_the_key_version_is_2` (spec 014).
- Processes (008), the node API (009), the scheduler (010), files (011), modules (012), and the
  narrowed device, vendor and OS names (013).
- JavaScript tests in the committed list: both ends are read from source, which needs no list.
- Protecting `main`, which waits on the owner ([handoff](../../docs/handoff.md)).

## Open questions

1. **AGENTS.md rule 6.** Proposed: "**Tests are not negotiable.** Never delete or weaken a test to
   make a check pass. A test or a case may be removed, skipped or emptied only when the spec lists
   it under "- Removed", with the reason, and renamed only when the spec lists it under
   "- Renamed". `node scripts/test-guard.js` enforces this in the hooks and in CI, over every
   collected case; it needs the engine environment." So a small fix with no spec can no more rename
   or skip a test than it can remove one today. *Recommended: yes, as worded:* it is today's rule,
   applied to what the guard can now see.
