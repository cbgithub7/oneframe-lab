# 007: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **Name the cases.** Give explicit `ids` (or `pytest.param(..., id=...)`) to the seven
  functions whose case IDs pytest numbers by position, and to
  `test_failures_are_classified_and_leave_nothing_cached`. Bodies do not change, and today's guard
  sees the same function names.
- [x] 2. **Refuse positional IDs.** Add `engine/tests/fixtures/listing.py` with the collection hook
  that refuses a case numbered by its position, naming the test, and set
  `strict_parametrization_ids = true` in `engine/pyproject.toml`. `engine/tests/conftest.py` imports
  the hook and enables pytester. Add `engine/tests/test_listing.py` with
  `test_a_case_numbered_by_its_position_is_refused`, which covers a duplicate ID too (AC5).
- [x] 3. **The listing.** Add the second hook, switched on only when the guard asks: each ID with
  its skip marks and calls and its empty flag, sorted. Add
  `test_the_listing_of_a_made_up_suite_is_as_expected` (AC3).
- [x] 4. **Which lines count.** `allowances()` reads "- Removed" and "- Renamed" lines from the
  Tests sections of specs whose folder changed since the merge base (committed, in the working
  tree, or untracked), skips the template and no longer reads `plan.md`; a Renamed line acts once
  its old name is gone. Node tests for AC6 and AC7; the existing guard tests stay as they are.
- [x] 5. **JavaScript entries.** `jsEntries()` reads tests where a statement starts, with their skip
  and empty flags; `testNames` reads JavaScript through it. Node test for AC4.
- [x] 6. **Compare IDs.** `collectPython()` and `problems()`: gone IDs, gained flags (renamed tests
  compared with their old flags), renames to nothing. The base list comes through `git show`, with
  the fallback to source names when it is missing; a failed collection fails the guard, saying why.
  The header lists what the guard cannot see. Node tests for AC2, the fallback, and the failed
  collection.
- [x] 7. **The committed list.** `--write`, and the failure on a list that differs from the live
  collection. Node tests for AC1 and AC8, each in a throwaway git repository with a stand-in
  collection. Run `node scripts/test-guard.js --write` and commit `engine/tests/collected.txt`.
- [x] 8. **Docs.** `AGENTS.md` rule 6 as the owner answered open question 1, and the guard's line
  under Commands; `specs/README.md`; the template's "- Renamed: none" line; the PR template's
  "Tests removed" section; the guard's item in `docs/roadmap.md`.
- [ ] 9. **PR.** Run Verification's demonstration, note the guard's time on Windows from CI, and
  check each criterion by name.

  Left for task 9: the demonstration and every criterion are in PR #28; the guard's time on Windows
  comes from that PR's CI run, then task 9 is ticked.
