# 014: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds or renames a test rewrites `engine/tests/collected.txt` with `node scripts/test-guard.js
--write` (007). No test's name or case ID changes but the one in task 7.

- [x] 1. **Store and host helpers.** `engine/tests/fixtures/store.py` with `sha`, `put` and the two
  pin-row builders; `FAST` and `TRANSFER` in `fixtures/hosts.py`, whose `sha256` becomes `sha`. The
  model tests, `test_archives.py` and 011's `test_runtime_sources.py` import them and drop their
  copies.
- [x] 2. **Small shared helpers in `conftest.py`.** The `hub` fixture, `call`, `wait_until` (which
  `wait_free` uses) and `held_elsewhere`. The five lock holders and the hand-written deadline loops
  outside the engine clients use them.
- [x] 3. **In-process engines.** The `make_engine` fixture, each engine shut down at teardown, and
  `engine` built on it. Every test that made an `Engine` asks `make_engine`, `tiny_engine` and
  `test_formats.py`'s helper included; the catalog writes its nodes before its engine starts.
- [x] 4. **One engine-process client.** `fixtures/engine_process.py` with `EngineProcess`, and the
  `engine_process` fixture, each process killed at teardown if still running. It replaces `Server`,
  `Client` and `_Engine`. Add `test_an_engine_process_that_says_nothing_fails_the_wait_by_name`.
  AC4's and AC5's commands print nothing.
- [x] 5. **The killed install.** Its second `runtimes.install` reply is asserted before any wait
  (AC6).
- [x] 6. **Bounds**, test by test as the spec's Tests section says; each bound kept carries a
  `# bound:` comment saying why it is evidence (AC7).
- [ ] 7. **Behaviour, not details.** Rename `test_the_key_version_is_2` to
  `test_a_record_made_under_another_key_version_is_never_served`, checking what the record does.
  Rewrite the two `_claimed` and `_wait` tests through `pin_tool` and `download`, and read what the
  tools say from standard error in every pin test. AC8's command prints nothing.
- [ ] 8. **Exceptions as data.** `outside` in `contracts/layout.json`, one entry per exception
  AGENTS.md lists, and its `_comment`. `engine/tests/fixtures/footprint.py` with the snapshots and
  the sandbox diff as functions; `test_footprint.py` uses them and allows the entries for the
  engine's tree; the registry check as a function too. Add AC1's test,
  `test_every_exception_says_who_writes_it_where_and_why` and `test_a_registry_change_is_reported`.
  AGENTS.md's "One data root" in the owner's wording (question 2), and `docs/architecture.md`'s.
- [ ] 9. **The real profile**, as the owner answered question 1. With (c): the watched list and the
  deep profile check in `fixtures/footprint.py`, replacing the top-level one; AC2's and AC3's tests,
  `test_a_file_rewritten_in_a_watched_folder_is_seen` and `test_a_watched_folder_that_appears_is_seen`.
  With (a): nothing. With (b): nothing here; the spike and its spec follow.
- [ ] 10. **Docs and the PR.** `docs/roadmap.md` loses the Tests items this spec took on (and the
  clean-up section, if nothing is left in it), and `docs/handoff.md` its open item on the footprint
  test. The PR carries what the spec's Verification asks.
