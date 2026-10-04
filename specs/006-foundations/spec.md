# 006: Foundations

Status: implemented 2026-10-03, in review (every task ticked; the local checks below wait for the merge)
Owner approval: 2026-10-03

Comes before spec 003's core, which builds on it. It answers [the review of 2026-10-02](../../docs/reviews/2026-10-02.md):
section 8B, section 5's escapes from the root and shared roots, and items 4.4, 4.6 to 4.8 and
part of 4.5. The rest of section 4 is tracked in the [handoff](../../docs/handoff.md). Reviewed on
2026-10-04 ([review](../../docs/reviews/2026-10-04-spec-006.md)).

## Problem

What the app writes, and how it fails, is not yet something a person can trust:

- It writes outside its data root: Electron to Roaming, jobs to temp, bytecode into `nodes/`, and
  soon model libraries to the home folder.
- Two processes on one root can break each other's work.
- No file says its format, so an older app can overwrite a newer file, and a moved root still
  reports its runtimes installed.
- Two places compute the root, and they disagree.
- Failures come in four shapes, with no file a person can hand over.
- The cache serves results from before a node's code or its runtime changed.

## Requirements

1. **One root, defined once.** One function in the engine and one in the app take the environment,
   platform, home folder and `packaged`, and a shared table of cases holds them equal.
    - Only the app chooses the packaged root (from `app.isPackaged`), and it always passes it with
      `--data`.
    - The engine's own default, which the command-line tools use, is the dev root:
      `OneframeLab-dev` (`oneframe-lab-dev` on Linux).
    - An empty or relative `XDG_DATA_HOME` is ignored.
2. **Nothing is written outside the root.** Design lists what this covers:
    - Electron's folders;
    - job, temporary and bench scratch folders;
    - the engine's bytecode;
    - the caches model libraries keep.

   The single-instance lock is then per root, and the page keeps nothing in Electron's session
   folder.
3. **Every kept file says its format, and nothing newer is overwritten.**
    - `oneframe-root.json` holds the layout's format and whether a dev checkout or a packaged app
      made it. The engine refuses to start on a root whose layout is newer, saying so.
    - Settings without `format` are format 1. Newer settings are read as the defaults, with a note
      saying why.
    - Learned memory in a newer format is left alone, and not used.
    - A runtime marker records its format and the environment's path:
        - one with a newer format reads as `newer_format`, and installing or removing that runtime
          is refused;
        - one whose path no longer matches (after both are resolved and their case normalised)
          reads as `moved`.
4. **Processes on one root leave each other's work alone.**
    - Each process keeps its temporary and job folders in a folder of its own under `cache/tmp/`,
      locked while it lives. At start, a process removes only the folders whose lock is free.
    - Installing or removing a runtime holds that runtime's lock across processes; a second is
      refused with `locked`.
    - Locks are released by the operating system when a process dies, and a lock file is never
      deleted.
5. **One failure model.**
    - Kinds and reasons are declared once, in `errors.py`, and an undeclared one fails a test.
    - Every failure the engine reports (node failures, install failures, error replies) carries:
        - a `kind`;
        - a snake_case `reason` where it has one;
        - a `message`;
        - `next` where it is known;
        - `retry`: true when the same request may succeed later unchanged.
    - The page receives all of them.
    - Runtime reasons become `not_installed`, `out_of_date`, `installing`, `blocked`, `unknown`,
      `moved`, `newer_format` and `locked`.
    - The one `Stopped` is child.py's, re-exported.
6. **Failures leave evidence.** Every event of a run or an install, from the app or a tool, is
   appended to a journal under `logs/`. Journals and per-step logs are pruned to a count and a size
   set in code.

   `npm run diagnose` writes one file to `reports/`, holding versions, the machine profile, the
   runtimes, settings, what was learned, and the latest journals and logs. It contains no
   environment variables. The user's segment of any path under the users folder is removed, in
   every spelling, and `hf_` tokens are scrubbed.
7. **Results follow code.** A node's cache key includes:
    - a hash of its folder's files, bytecode left out;
    - for a runtime node, its build and the hashes its marker records (lock, definition and
      stand-ins).

   `KEY_VERSION` becomes 2. Old entries stay on disk until a later spec adds eviction.

## Acceptance criteria

- [ ] AC1: **The root table.** Its rows include an empty, missing or relative value for each
  variable, Windows rows written with `path.win32` and `PureWindowsPath`, and dev and packaged
  roots. Python and JavaScript give the same root for every row (tests in both suites, on both
  operating systems).
- [ ] AC2: **Footprint** (CI, Windows and Linux).
    - Point HOME, USERPROFILE, APPDATA, LOCALAPPDATA, XDG_*, TMP, TEMP and TMPDIR into a sandbox,
      and copy the test nodes and the tiny runtime's definition into it.
    - Then install the runtime (uv fetches its Python into the new root), run a runtime node, and
      start and stop the engine.
    - Afterwards nothing new exists in the sandbox outside the root.
    - On Windows, the top level of the real profile folders gains nothing, and neither
      `HKCU\Software\Python` nor `HKCU\Environment\Path` changes.
    - The child's environment puts every variable Design lists under the root.
- [ ] AC3: `boot(app)` sets user data, session data, crash dumps and the log path under the root
  before it asks for the single-instance lock (a unit test with a recording fake `app`).
- [ ] AC4: **Formats** (tests):
    - a root whose layout is newer stops the engine with a message;
    - newer settings are read as the defaults, with a note;
    - newer learned memory and a newer marker are left byte for byte, and the marker reads as
      `newer_format`;
    - a runtime environment moved to another root reads as `moved`.
- [ ] AC5: **Two processes** (subprocess tests). A helper process holds a temporary folder's lock
  and a runtime's lock.
    - An engine that starts on the root keeps that folder, and refuses with `locked` to install or
      remove the runtime.
    - Once the helper is killed, the next start removes the folder, and the install runs. Windows
      releases locks late, so the test polls.
- [ ] AC6: **Failures.** Raising an undeclared kind or reason fails a unit test. A test holds the
  declaration in errors.py and the table in [architecture.md](../../docs/architecture.md) equal,
  both ways. An error reply's `kind`, `reason` and `next` reach the page: `main.js` returns them as
  a value (app test).
- [ ] AC7: **Diagnostics** (tests).
    - A run's events are in its journal, in order.
    - Pruning keeps its bound.
    - Plant the home path in every spelling (backslashes, forward slashes, JSON-escaped, other case,
      and an 8.3 short name), and an `hf_` token, in a journal line and a setting. The file
      `npm run diagnose` writes contains none of them, and keeps a user name that is also an
      ordinary word elsewhere.
- [ ] AC8: **Cache keys** (unit tests).
    - Changing a file in a node's folder changes its key.
    - So do changing its runtime's lock or definition, or its build: cpu against cu130.
    - Bytecode does not change it, and the same code and runtime give the same key.

## Design

- **`oneframe/layout.py`** names every path under the root; `app/main/paths.js` mirrors it, and a
  JSON table drives both test suites. `child_env` sets, after the forced variables:
    - under `cache/runtime/<id>/`: `HF_HOME`, `TORCH_HOME`, `TORCH_EXTENSIONS_DIR`,
      `TORCHINDUCTOR_CACHE_DIR`, `TRITON_HOME`, `CUDA_CACHE_PATH`, `XDG_CACHE_HOME` and
      `MPLCONFIGDIR`;
    - under the job's own folder: `TMP`, `TEMP` and `TMPDIR`;
    - `PYTHONDONTWRITEBYTECODE=1`, so children read the bytecode compiled at install and write
      none.

  The engine sets `sys.pycache_prefix` to `cache/pycache/` for itself, and its own temp folder
  (which uv inherits) is its `cache/tmp/` folder. Spec 003 points `HF_HUB_CACHE` at its views.
- **`app/main/boot.js`** holds `boot(app)`, which `main.js` calls first.
- **The top-level layout keeps today's folders,** adding `oneframe-root.json`, `electron/`,
  `cache/electron/`, `cache/jobs/`, `cache/runtime/`, `cache/pycache/`, `logs/crashes/`,
  `logs/locks/` and `cache/CACHEDIR.TAG`. A root without `oneframe-root.json` is format 0; the
  engine writes it on first start. No file moves.
- **One JSON writer** writes to a unique temporary file, flushes it and replaces the old one, and
  refuses to replace a newer format.
- **Locks** use `msvcrt.locking` or `fcntl.flock` (not `lockf`, which lets one process take the
  same lock twice) on files under `logs/locks/`, which are never pruned.
- **The journal** wraps `emit` where runs and installs start (`Scheduler.run`,
  `Runtimes.run_install`), so it covers the command-line tools as well as the app.

## Tests

- Added: one or more per acceptance criterion.
- Changed: `test_a_store_file_that_cannot_be_read_starts_empty` (test_memory.py) asserted that a
  learned-memory file in a newer format is replaced; requirement 3 leaves it as it is, so it now
  asserts that, and still that an unreadable file is replaced.
- Changed: the runtime tests in test_runtime_install.py and test_runtime_server.py compare statuses
  and reasons with the snake_case spellings requirement 5 sets (`not_installed`, `out_of_date`).
- Changed: "the data root is one folder per platform, overridable" (tests/app/engine.test.js) calls
  the root function as requirement 1 defines it and expects the dev root's name; its cases moved,
  with many more, to layout.test.js, which checks the shared table.
- Removed: none.

## Verification

AC1, AC2 and AC4 to AC8 run in CI on Windows and Linux; AC3 runs as a unit test, since CI never
starts Electron. In a local session after the merge:

- `npm start` creates nothing in `%APPDATA%\oneframe-lab`;
- the old roots are deleted by hand: `%LOCALAPPDATA%\OneframeLab`, `%APPDATA%\oneframe-lab`, and,
  on Linux, `~/.local/share/oneframe-lab` and `~/.config/oneframe-lab`. `OneframeLab` becomes the
  packaged app's name;
- the torch runtime is installed into the dev root with `npm run bench:runtime -- torch`;
- the local-session skill's mention of the old root is updated (a task; done in task 11).

The exact steps, on Windows (`/local-session`), each one's output kept for the report
`specs/006-foundations/reports/<date>-local.md`:

1. Note what exists: `dir %APPDATA%`, `dir %LOCALAPPDATA%`, `dir %USERPROFILE%`, and
   `reg query HKCU\Software\Python /s` and `reg query HKCU\Environment /v Path`.
2. `npm start`; wait for the node list; close the app. Then `dir %LOCALAPPDATA%\OneframeLab-dev`
   shows `electron`, `cache`, `logs`, `oneframe-root.json`, and `%APPDATA%\oneframe-lab` does not
   exist (delete it first if an older app made it).
3. `npm run bench:runtime -- torch`: the report names the dev root, and the install's journal is in
   `%LOCALAPPDATA%\OneframeLab-dev\logs\journal\`.
4. `npm run diagnose`; open the file it names in `reports\`, and search it for the Windows user
   name: it appears only as an ordinary word, never in a path.
5. Repeat step 1 and compare: nothing new at the top of the three profile folders, and the two
   registry values unchanged.
6. Delete the old roots by hand (above).

## Decisions taken

Each is reversible; the numbers and names live in the code, which says why beside them.

- **The dev root is renamed now, not migrated**: one torch reinstall, and the clean name stays for
  the packaged app. **The top-level layout stays**: regrouping it would need a migration and buy
  nothing yet. **A root of the other kind**, reached through `ONEFRAME_DATA`, is used, with a
  warning in `engine.ready`.
- **Every variable that names the root's folder must be absolute** (task 1), as XDG says of its
  own: the root functions take no working folder. **The shared table is `contracts/layout.json`**,
  and both languages clean paths by one rule written out twice, because `ntpath` and Node's
  `path.win32` disagree on shares and leading double slashes.
- **Formats** (tasks 2, 3): learned memory keeps `version` as its format key, as it was; the root
  file records `format` and `kind`, and one that cannot be read stops the engine as a newer one
  does, since it might be one; a marker without `format` is format 1, one without `env` is never
  `moved`, and installing over a `moved` environment deletes it first. Cache records get no format:
  `KEY_VERSION` makes an old record a miss. **A format goes up only when an older app would misread
  or damage the file**; a new file or folder is not a format change
  ([architecture.md](../../docs/architecture.md)).
- **Each process's folder is a numbered `cache/tmp/<n>/` with its lock** (task 4): numbers are
  reused, so the lock files, never deleted, stay few; anything else in `cache/tmp/` predates this
  spec and is removed. Design's `cache/jobs/` is not made, since requirement 4 puts job folders in
  the process's own folder. A lock error other than "held" is an error, not busy. `bench:fit`
  writes its report to `reports/` unless `--out` names a place.
- **What keeps a child under the root is reserved** (task 5): a runtime's definition cannot set the
  library-cache, temporary or bytecode variables, and the person's own hub and Triton cache
  variables, which would override them, never reach a child. The app sets `PYTHONPYCACHEPREFIX`
  for the engine's own modules; uv and children never see it.
- **The app's own temporary folder is `cache/electron/tmp/`** (task 6), outside `cache/tmp/`, and the
  `uv run` that starts the engine uses it too. **The page's session is in memory.** **The
  spellchecker is off with no languages**, found by running the real app: otherwise each start
  fetched dictionaries from Google, and turning it off alone did not stop that. **A second launch
  on a root opens no window and starts no engine.** **A `TMPDIR` too long for Chromium's socket is
  set aside while the lock is asked for**: it aborted the app at start (with the owner's
  decision 3; an Electron upgrade re-checks the socket's path, [versions.md](../../docs/versions.md)).
- **Failures** (task 8): kinds beyond the review's list are `edge`, `graph`, `root`, `request` and
  `app`; refusals to install or remove share kind `runtime`. `next` is a sentence for a person and
  `retry` belongs to the reason; the page decides from the kind and reason. An `edge` failure has no
  `node.failed`: `run.failed` names the edge. The page receives a failed request as a value, since
  Electron passes on only a thrown error's message; a page that subscribed after the engine failed
  to start learns why from its first request. Any start failure is an `engine.failed` with a reason.
- **Evidence** (task 9): one journal per run or install, pruned as a folder with the step logs
  (now `logs/steps/`), so `app.log` is never touched. A journal that cannot be written never fails
  the run; a full one still takes how the run ended, and an engine crash is its last line. The
  diagnostics file is JSON, redacted after it is serialised, and matches a user only as a whole
  path segment after the users folder, so a longer name or a word elsewhere is kept. `diagnose` reads
  the root as it is: it reports on a root the engine refuses, and writes only `reports/`.
- **Cache keys** (task 10): a node's code is read from its files each run (a size and a time can
  stay the same when the bytes do not), following linked folders; the runtime part is the build the
  plan picks and the hashes its marker records, so a key follows what is installed. A cached result
  is served even when the runtime is `out_of_date` since: it is what the installed build made. A
  node in the engine's process re-imports its helpers, as its key says. **`KEY_VERSION` goes up when
  the engine changes what a node's output means** (its trust, facets or carrier handling), since
  the engine's own code is in no key.

## Out of scope

- **Packaging:** guarded deletes and `--remove-data`, one app id, a clean-VM test, and first-run
  checks for long paths and OneDrive.
- **Cache eviction**, and a size cap.
- **Splitting the child into setup and per-job code,** before spec 004.
- **A machine-readable protocol contract.**

## Open questions

None.

## Owner's decisions

1. **The storage rule's exceptions** (2026-10-03). "Nothing in the repo" is kept, except a file the
   person names (such as a bench report written with `--out` into `specs/<id>/reports/`) and a dev
   checkout's engine environment (`engine/.venv`). [AGENTS.md](../../AGENTS.md) says so.
2. **The two changed tests** (2026-10-04, approved). `test_a_store_file_that_cannot_be_read_starts_empty`
   now asserts that learned memory in a newer format is left as it is (requirement 3), and still that an
   unreadable file is replaced; the runtime tests compare the snake_case reasons (requirement 5). Both
   follow from this spec's requirements, and neither checks less than before. A third, in
   `tests/app/engine.test.js`, was unlisted until the review; it is listed under Changed, with the
   review's fixes the owner accepted the same day.
3. **Chromium's files in the system temporary folder on Linux** (2026-10-04, accepted as an
   exception; [AGENTS.md](../../AGENTS.md) says so). While the app runs, Chromium keeps the socket
   behind the single-instance lock in a folder it makes in `$TMPDIR` or `/tmp` (`scoped_dir*`, linked
   from `electron/`), removed at a clean exit and left by a crash until the system cleans its
   temporary folder; it also makes and deletes one temporary file there. Windows uses no file for the
   lock. A study on Electron 44.4.5 (task 6's open question, answered with the source and real runs)
   found the socket *can* be moved, by setting `TMPDIR` before the lock, contrary to what this spec
   first said; it stays where Chromium puts it because a socket path over 107 bytes aborts the app at
   start (a root under `cache/electron/tmp` leaves only 55 bytes for the root's own path), and a root
   on a network file system may not hold a socket at all, which would make the app quit without a word.
4. **Graphics driver shader caches** (2026-10-04, accepted as an exception on the review's
   recommendation; [AGENTS.md](../../AGENTS.md) says so). The GPU process writes the driver's shader
   cache where the driver keeps it (`~/.cache/mesa_shader_cache` with Mesa, `~/.cache/nvidia` or
   `%LOCALAPPDATA%\NVIDIA\DXCache` with NVIDIA, `D3DSCache` on Windows). The driver owns, caps and
   shares it with every program on the machine. The app's own code cannot redirect it: on Linux the
   GPU process forks from a zygote that starts before `main.js`, so a variable set there never
   reaches it, and on Windows the driver alone decides where its caches go. On Linux a launcher,
   or the app relaunching itself, could set `MESA_SHADER_CACHE_DIR` (and, untested, NVIDIA's
   `__GL_SHADER_DISK_CACHE_PATH`) before Electron starts; the review first said it could not be
   moved. Told this, the owner kept the exception (2026-10-04): the driver caps the cache, every
   program shares it, and a relaunch would cover only part of Linux.
