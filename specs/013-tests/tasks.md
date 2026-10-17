# 013: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. No test's name
or case ID changes but the one in task 7, so 007's guard sees only moves until then.

- [ ] 1. **Store and host helpers.** `engine/tests/fixtures/store.py` with `sha`, `put` and the two
  pin-row builders; `FAST` and `TRANSFER` in `fixtures/hosts.py`, whose `sha256` becomes `sha`. The
  model tests and `test_archives.py` import them and drop their copies.
- [ ] 2. **Small shared helpers in `conftest.py`.** The `hub` fixture, `call`, `wait_until` (which
  `wait_free` uses) and `held_elsewhere`. The five lock holders and the hand-written deadline loops
  outside the engine clients use them.
- [ ] 3. **In-process engines.** The `make_engine` fixture, each engine shut down at teardown, and
  `engine` built on it. Every test that made an `Engine` asks `make_engine`, `tiny_engine` and
  `test_formats.py`'s helper included; the catalog writes its nodes before its engine starts.
- [ ] 4. **One engine-process client.** `fixtures/engine_process.py` with `EngineProcess`, and the
  `engine_process` fixture, each process killed at teardown if still running. It replaces `Server`,
  `Client` and `_Engine`. Add `test_an_engine_process_that_says_nothing_fails_the_wait_by_name`.
  AC4's and AC5's commands print nothing.
- [ ] 5. **The killed install.** Its second `runtimes.install` reply is asserted before any wait
  (AC6; the temporary change that shows it is described in the PR, not committed).
- [ ] 6. **Bounds**, test by test as the spec's Tests section says. AC7's command prints only the
  five kept bounds, each with its reason in the PR.
- [ ] 7. **Behaviour, not details.** Rename `test_the_key_version_is_2` to
  `test_a_record_made_under_another_key_version_is_never_served`, checking what the record does.
  Rewrite the two `_claimed` and `_wait` tests through `pin_tool` and `download`, and read what the
  tools say from standard error in every pin test. AC8's command prints nothing.
- [ ] 8. **Exceptions as data.** `outside` in `contracts/layout.json`, one entry per exception
  AGENTS.md lists, and its `_comment`. `engine/tests/fixtures/footprint.py` with the snapshots and
  the sandbox diff as functions; `test_footprint.py` uses them and allows the entries for the
  engine's tree. Add AC1's test and `test_every_exception_says_who_writes_it_where_and_why`.
  AGENTS.md's "One data root" in the owner's wording (question 2), and `docs/architecture.md`'s.
- [ ] 9. **The real profile**, as the owner answered question 1. With (c): the watched list and the
  deep profile check in `fixtures/footprint.py`, replacing the top-level one; AC2's and AC3's tests.
  With (a): nothing. With (b): nothing here; the spike and its spec follow.
- [ ] 10. **Docs and the PR.** `docs/roadmap.md` loses the Tests items this spec took on, and
  `docs/handoff.md` its open item on the footprint test. The PR pastes AC4, AC5, AC7 and AC8's
  output and the footprint test's time on Windows before and after.
