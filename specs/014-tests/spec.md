# 014: Tests that check behaviour

Status: approved
Owner approval: 2026-10-18, every open question answered as recommended
Changed after approval (from `/code-review`, 2026-10-18), approved by the owner 2026-10-23: AC5 also
  catches an engine named or opened another way; AC7 asks each timing line to be a marked bound,
  rather than counting five.

The eighth of eight clean-up specs (007 to 014), from the
[review of 2026-10-12](../../docs/reviews/2026-10-12.md) (sections 4 and 6), the
[roadmap](../../docs/roadmap.md)'s "Tests" items and the [handoff](../../docs/handoff.md)'s
footprint item. It needs only 007, whose guard sees its rename, and is worked last.

## Problem

- **The footprint test** compares only top-level names in the real `USERPROFILE`, `APPDATA`,
  `LOCALAPPDATA` and `HOME`. It failed once on a runner's Firefox (PR #11: a new `Mozilla` in
  Roaming), and cannot see a write into a folder that already exists, where a program that ignores
  the environment writes. The data root's exceptions are AGENTS.md prose the test never reads; on
  2026-10-12 the owner asked for them as data.
- **Helpers are copied:** `sha` six times; `put` twice; four pin-row builders for two kinds of
  row; the `hub` and in-process `engine` fixtures and `call` twice; the fast policy and the
  transfer options three times each; a lock held by another process five times; deadline loops by
  hand in five files; and three clients for the engine as a process. One, `Client`, blocks on
  `readline`, so its timeout never fires.
- **Engines outlive tests.** Five places start an in-process engine and never shut it down, the
  `tiny_engine` fixture among them. The killed-install test leaves its first engine process
  running if a step fails before `kill`, and never checks its second `runtimes.install` reply: a
  refusal shows only as a 180 s wait for an ending.
- **Nine assertions bound wall-clock time**, some timed from before the event they measure, some
  telling nothing the test's other assertions do not.
- **Two tests check details:** `test_the_key_version_is_2` asserts a constant, and two pin tests
  call the tool's private `_claimed` and `_wait`; seven pin tests patch its private `_print`.

## Requirements

1. **Exceptions are data.** `contracts/layout.json` gains `outside`, one entry per data-root
   exception AGENTS.md lists when this lands. Each names its writer (the app, the engine, a runtime,
   a tool, a dev checkout or the graphics driver), its systems, what it is and why, and any fixed
   paths, spelled from the environment or the checkout. AGENTS.md points to it (question 2).
2. **The footprint test reads them.** A write outside the root passes only at a path an entry
   names for what the engine's process tree runs: the engine, a runtime, the driver.
3. **The real profile is checked where our programs write** (question 1, option c). The test
   watches a list, kept as data with each entry's program, of the default folders of what the
   engine's tree runs (uv's cache, Pythons, executables, tools and settings; CPython's per-user
   folder). Anything new or changed under a watched folder fails it, as does a watched folder that
   appears; a new name elsewhere in the profile does not. The sandbox diff and the registry check
   stay as they are, and every check is a function a test can show failing.
4. **One copy of each helper, and every engine ended.** Each helper the Problem names lives once,
   in `engine/tests/conftest.py` or `engine/tests/fixtures/`. Every engine a test starts, in-process
   or as a process, comes from a fixture that ends it at teardown, pass or fail. Every wait has a
   deadline, past which it fails naming what it waited for.
5. **Wall-clock bounds are evidence or go.** A bound stays where a spec requires a time (spec 003's
   2 s Stop), or where nothing else in the test tells the path under test from a slower one. Then
   its clock starts at the event that starts the path, and the slower path takes at least ten times
   the bound. A test that could hang ends at a deadline that fails it by name instead.
6. **Tests check behaviour.** The killed-install test asserts its second install's reply. The
   key-version test checks that a record made under another key version is never served. The tool
   tests drive `pin_tool` and `download`, and read what a tool says from its standard error.
7. **Nothing else changes:** no engine or app code, nothing a person sees, and no test's name or
   case ID but the one renamed.

## Acceptance criteria

- [ ] AC1: `test_a_write_an_exception_names_passes_only_while_it_is_listed`: a write at a path an
  `outside` entry names for the engine is not reported; the same write is reported once the entry
  is removed, or names the app as its writer.
- [ ] AC2: `test_a_write_inside_a_watched_folder_that_exists_is_seen`: a file made two levels inside
  a watched folder that existed before is reported. Today's check misses it. Only with option (c).
- [ ] AC3: `test_a_new_folder_outside_the_watched_list_is_not_reported`: a new top-level folder in a
  profile, as the runner's Firefox made, is not reported. Today's check reports it. Only with (c).
- [ ] AC4: `rg -n '^(def (sha|_sha|put|pin|pinned|hub_pinned|call|hub|engine|wait_until)\b|class (Server|Client|_Engine)\b|(FAST|TRANSFER)\b.*= )|monotonic\(\) \+|FileLock\(Path\(sys\.argv' engine/tests --glob '!conftest.py' --glob '!**/fixtures/**'`
  prints nothing. Today it prints 41 lines in 10 files.
- [ ] AC5: `rg -nP '^(?!\s*f?["\x27]).*\b(Engine|EngineProcess)\(' engine/tests --glob '!conftest.py' --glob '!**/fixtures/**'`
  prints nothing: outside a script run in a Python of its own, every engine comes from a fixture
  that ends it, however it is named or opened. Today it prints 13 lines.
- [ ] AC6: `test_a_killed_install_is_not_installed_and_a_second_install_completes` fails on the
  second reply, naming it, when that reply is an error: shown once in the PR with a temporary
  change that makes the second install refuse, then reverted. Today it fails only after 180 s.
- [ ] AC7: `rg -n 'monotonic\(\) -' engine/tests` prints only lines marked `# bound:`, each in a
  test the Tests section keeps a bound in, and the PR shows its output; a later bound is marked
  too. Today it prints nine, none marked.
- [ ] AC8: `rg -n 'tools\._|tools, "_|assert KEY_VERSION' engine/tests` prints nothing. Today it
  prints six lines, in `test_model_pin.py` and `test_cache_keys.py`.

## Design

**The footprint test.** `outside` sits beside `roots` and `paths` in `contracts/layout.json`; the
two layout tests do not read it, so the app's side is unchanged. `fixtures/footprint.py` holds the
folder snapshots (names, sizes and times), the sandbox diff and its allowed paths, the profile
check and its watched list, and the expansion of an entry's paths in a given environment (the
sandbox's or the real one). `test_footprint.py` keeps its run and name and calls them; their unit
tests use folders they make.

**Helpers**, one copy each:
- `fixtures/store.py`: `sha` (`hosts.py`'s `sha256` becomes it); `put`, bytes placed as a finished
  download would; a pin row for a URL file and one for a hub file, the format a parameter.
- `fixtures/hosts.py`: `FAST` and `TRANSFER`, the options every download test passes.
- `fixtures/engine_process.py`: `EngineProcess`, the engine as a process, reading stdout on a
  thread, with `call`, `event`, `wait` with a deadline, `seen`, `close` and `kill`. It starts the
  command it is given, so a test starts the engine as it needs.
- `conftest.py`: the fixtures `hub`, `make_engine` (in-process engines, each shut down at
  teardown), `engine` (one of those, for the model tests) and `engine_process` (each killed at
  teardown if still running); `call`; `wait_until`, which `wait_free` uses, so no test loops to a
  deadline of its own; `held_elsewhere`, a lock another process holds until it lets go or is killed.

**Behaviour, not details.** The key-version test raises `cache.KEY_VERSION` by one, then checks
that the key changes and that `Cache.get` misses the stored record. The tool tests refuse a claim
with a real holder, and wrap the public `claim_store` the tool calls for a Ctrl+C during a claim
that is made. They assert the exit code, that the store was let go, and that Ctrl+C is the tool's
own again.

**Docs that change:** `AGENTS.md`'s "One data root" (question 2); `docs/architecture.md`'s, whose
exceptions become those `contracts/layout.json` lists; `docs/roadmap.md`, whose Tests items go, and
its clean-up section once empty; `docs/handoff.md`, its footprint item.

**Risks:**
- Option (c) no longer sees a new name made by a program off the list. The list grows with each
  new kind of program a run starts, in the spec that adds it.
- On a dev box a watched folder can be large (a uv cache), and uv used elsewhere during the test
  fails it, naming the folder. CI runs nothing else.
- Helpers move across ten files; no test moves, so every ID 007's guard collects stays the same.
- Specs 008 to 012 change some of the same tests (the Stop test, sources, waits on `engine._run`).
  Worked last, this spec builds on what they leave; landed earlier, they would adapt instead.

## Tests

- Added:
    - `test_footprint.py`: the tests named in AC1 to AC3,
      `test_every_exception_says_who_writes_it_where_and_why`, `test_a_registry_change_is_reported`
      and, with (c), `test_a_file_rewritten_in_a_watched_folder_is_seen` and
      `test_a_watched_folder_that_appears_is_seen`;
    - `test_server_and_nodes.py`: `test_an_engine_process_that_says_nothing_fails_the_wait_by_name`.
- Renamed: `test_the_key_version_is_2` -> `test_a_record_made_under_another_key_version_is_never_served`
- Changed, names kept:
    - `test_a_killed_install_is_not_installed_and_a_second_install_completes`: AC6, and the
      fixture ends its first engine;
    - bounds kept, already meeting the rule: spec 003's two Stop tests (in the transport's and the
      jobs' tests) and `test_a_stall_holds_the_connection_until_released`;
    - bounds timed from their trigger, the slower path ten times longer:
      `test_stop_kills_a_runtime_child` (from the stop) and
      `test_a_tool_waits_for_a_store_another_process_still_holds_then_runs` (from the let-go);
    - a deadline instead of a bound, 008's `stop_after`:
      `test_a_node_that_reads_stdin_gets_end_of_file_at_once`;
    - bounds that go, since other assertions tell the paths apart:
      `test_ctrl_c_while_a_tool_waits_for_the_store_ends_it_with_nothing_started` (the expiry
      cannot give STOPPED), `test_a_mistake_in_the_request_is_said_at_once_even_while_the_store_is_held`
      (nothing said it waits), `test_shutdown_stops_a_running_graph_and_its_node_process`
      (`run.stopped`, and the process gone);
    - through the tools: `test_a_tool_claims_again_until_the_store_is_let_go_saying_so_once` and
      `test_ctrl_c_during_a_refused_claim_ends_the_wait_and_during_a_made_one_is_passed_on`;
    - `test_ac2_nothing_is_written_outside_the_root`: Requirements 2 and 3.
- Removed: none.

## Verification

AC1 to AC3 and every test above run in CI on ubuntu-latest and windows-latest. The PR pastes the
output of AC4, AC5, AC7 and AC8's commands, and the footprint test's time on Windows before and
after. No step needs a GPU.

## Decisions taken

- `outside` lives in `contracts/layout.json`: the layout's contract is where a reader looks.
- `outside` keeps the file's `version`: a new key, read only by a test, misreads nothing.
- The watched list is the test's data, not the contract's: it is the test's net, not a promise.
- The checks get unit tests: the real run cannot show that a check fails when it should.
- Engines come from factories that end what they made: a test cannot forget, pass or fail.
- One engine-process client, reading on a thread: a wait ends at its deadline even in silence.
- A redundant bound goes rather than widens: it adds nothing but a way to fail on a slow runner.
- The tool tests time Ctrl+C by the tool's output, as a person does, not by its internals.
- No pytest-timeout: each wait has its own deadline, and a new dependency needs a reason.
- `sha` is `hashlib`'s, not 011's file helper: a test's expected value never comes from the code.
- `TRANSFER` carries `FAST`, as most copies did; the shutdown test whose stall must hold passes `Policy()`.

## Out of scope

- The test guard and explicit case IDs (007).
- `alive`, `wait_gone` and `stop_after`, Stop as an `Event`, and the Windows Stop hang (008).
- Waits on `engine._run`, which become waits on a run's ending (010).
- Runtime sources from spec 003's hosts, and starting an engine process with the test CA (011).
- The device, vendor and OS names outside v1 (013); `allow_network` (009).
- The app's own footprint (Electron's folders): the test runs the engine, as today.
- Other private names tests reach, such as `engine.models._job`: the review named these two.

## Open questions (answered by the owner, 2026-10-18)

1. **What does the footprint test check in the real profile?** (a) Keep today's check of new
   top-level names: flaky, and blind to writes into folders that exist. (b) Trace the files the
   engine's tree creates, deletes or renames with the system's own tools, in CI: checks the most,
   but needs administrator rights and a spike on the Windows runner first, keeping (a) until then.
   (c) Watch our programs' default folders, deep: sees writes into folders that exist, but no
   longer a new name made by a program off the list, and is slower on a dev box with a large uv
   cache. *Answered: (c):* it ends the flake without a spike and looks deeper where our
   programs write. With (a) or (b), AC2 and AC3 go; with (b) the profile check becomes its own
   spec after the spike.
2. **AGENTS.md, "One data root".** Proposed, after "holds the two equal": "The only exceptions are
   the entries of `outside` in `contracts/layout.json`, each naming who writes it, where and why.
   The footprint test allows nothing else, and a new entry is the owner's decision, as a change to
   this rule is." *Answered: yes, as worded:* the owner chose data on 2026-10-12, and the last
   sentence keeps exceptions the owner's.
