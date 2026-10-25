# 011: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land. A task that
adds, moves or removes a test rewrites `engine/tests/collected.txt` with
`node scripts/test-guard.js --write` (007).

- [x] 1. Recorded hashes, on today's code and in a commit of their own (AC1's diff starts here):
  `test_recorded_hashes.py` with its four cases, and `test_uv_folders_are_the_layouts_unless_named`.
- [x] 2. One hash: `files.CHUNK`, `files.sha256_file`, and `text_sha256` moved from `runtimes.py`.
  The cache, the store's `adopt`, jobs' verify, the transport's resume and `archives.sha256_of`'s
  callers use them; the old loops go. Move `test_hashing_stops_between_chunks` to `test_files.py`;
  the jobs test patches `jobs.sha256_file`.
- [x] 3. One move: `files.replace`, the store's rename with the folder rule, and
  `test_a_move_onto_an_existing_folder_is_never_tried_again`. `write_text`, `Store.place` (wrapping
  into `os_refused`), `archives.extract` and `cache.commit` use it; `_rename` and `_replace` go,
  the tests that patch them patch `files.replace`, and the store's placing test reads `os_refused`
  from `Store.place`.
- [x] 4. One free space: `files.free_bytes`, which `Store.free_bytes` and `hardware.disk_free_mb`
  call, and `test_free_space_is_read_from_the_nearest_folder_that_exists`.
- [x] 5. `Layout.objects` in `layout.py`, `app/main/paths.js` and `contracts/layout.json` together;
  `Cache(objects, work)` at every call site (the engine, `bench_fit`, the tests).
- [x] 6. A runtime's folder and the delete guard's root from `Layout.runtimes`; uv's folders decided
  once in `Runtimes.__init__`, and `uv_environment` and `install` take the two (`test_formats.py`'s
  direct call too). AC3's command prints nothing.
- [x] 7. The test side, with no change in behaviour: `Runtimes(transfer=)` and the engine passing
  it, `server.main(argv, transfer=)`, `engine/tests/fixtures/serve.py`, and the hosts' fault that
  serves a body with no length, and their record of a stall still held.
- [x] 8. Sources through the transport, code and tests in one commit: the source function, with
  `archives.download`, urllib and `opener` gone; `SourceServer` gone and every source test on the
  hosts (the server tests through the driver); the three download tests moved to
  `test_runtime_sources.py`; AC4 to AC6 and the no-length test; the removed test;
  `test_there_is_one_stopped` naming the transport. AC2's command prints only `files.py`.
- [x] 9. If the owner said yes to question 1: `runtime.json` refuses a source that is not https,
  `test_a_source_that_is_not_https_is_refused`, and the definition test's expected problem.
- [x] 10. Docstrings: the transport's tripwire line and "the drive"; `runtime_install`'s resume;
  `models/__init__.py`.
- [x] 11. Docs: `docs/architecture.md`'s three rows, `docs/runtimes.md`, the roadmap's items, and
  `AGENTS.md`'s "One data root" as question 3 was answered.
- [x] 12. The PR: AC1 to AC3's commands and AC8's diff pasted, `node scripts/test-guard.js` green,
  and each criterion checked by name.
