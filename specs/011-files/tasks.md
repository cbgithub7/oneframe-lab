# 011: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [ ] 1. Recorded hashes, on today's code and in a commit of their own (AC1's diff starts here):
  `test_recorded_hashes.py` with its four cases, and `test_uv_folders_are_the_layouts_unless_named`.
- [ ] 2. One hash: `files.CHUNK`, `files.sha256_file`, and `text_sha256` moved from `runtimes.py`.
  The cache, the store's `adopt`, jobs' verify, the transport's resume and `archives.sha256_of`'s
  callers use them; the old loops go. Move `test_hashing_stops_between_chunks` to `test_files.py`;
  the jobs test patches `jobs.sha256_file`.
- [ ] 3. One move: `files.replace`, the store's rename with the folder rule, and
  `test_a_move_onto_an_existing_folder_is_never_tried_again`. `write_text`, `Store.place` (wrapping
  into `os_refused`), `archives.extract` and `cache.commit` use it; `_rename` and `_replace` go,
  and the tests that patch them patch `files.replace`.
- [ ] 4. `Layout.objects` in `layout.py`, `app/main/paths.js` and `contracts/layout.json` together;
  `Cache(objects, work)` at every call site (the engine, `bench_fit`, the tests).
- [ ] 5. A runtime's folder and the delete guard's root from `Layout.runtimes`; uv's folders decided
  once in `Runtimes.__init__`, and `uv_environment` takes the two. AC3's command prints nothing.
- [ ] 6. The test side, with no change in behaviour: `Runtimes(transfer=)` and the engine passing
  it, `server.main(argv, transfer=)`, `engine/tests/fixtures/serve.py`, and the hosts' fault that
  serves a body with no length.
- [ ] 7. Sources through the transport, code and tests in one commit: the source function, with
  `archives.download`, urllib and `opener` gone; `SourceServer` gone and every source test on the
  hosts (the server tests through the driver); the three download tests moved to
  `test_runtime_sources.py`; AC4 to AC6 and the no-length test; the removed test;
  `test_there_is_one_stopped` naming the transport.
- [ ] 8. If the owner said yes to question 1: `runtime.json` refuses a source that is not https,
  `test_a_source_that_is_not_https_is_refused`, and the definition test's expected problem.
- [ ] 9. Docstrings: the transport's tripwire line and "the drive"; `runtime_install`'s resume;
  `models/__init__.py`.
- [ ] 10. Docs: `docs/architecture.md`'s three rows, `docs/runtimes.md`, the roadmap's items, and
  `AGENTS.md`'s "One data root" as question 4 was answered.
- [ ] 11. The PR: AC2, AC3 and AC8's commands and AC1's diff pasted, `node scripts/test-guard.js`
  green, and each criterion checked by name.
