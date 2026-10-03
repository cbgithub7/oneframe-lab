# 006: Foundations

Status: approved (drafted and agent-reviewed 2026-10-03)
Owner approval: 2026-10-03

Comes before spec 003's core, which builds on it. It answers [the review of 2026-10-02](../../docs/reviews/2026-10-02.md),
sections 4, 5 and 8B.

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
- Removed: none.

## Verification

AC1, AC2 and AC4 to AC8 run in CI on Windows and Linux; AC3 runs as a unit test, since CI never
starts Electron. In a local session after the merge:

- `npm start` creates nothing in `%APPDATA%\oneframe-lab`;
- the old roots are deleted by hand: `%LOCALAPPDATA%\OneframeLab`, `%APPDATA%\oneframe-lab`, and,
  on Linux, `~/.local/share/oneframe-lab` and `~/.config/oneframe-lab`. `OneframeLab` becomes the
  packaged app's name;
- the torch runtime is installed into the dev root with `npm run bench:runtime -- torch`;
- the local-session skill's mention of the old root is updated (a task).

## Decisions taken

- **The dev root is renamed now, not migrated.** The cost is one torch reinstall, and the clean
  name stays for the packaged app.
- **The top-level layout stays.** Regrouping it into config, state and cache would need a
  migration and buy nothing yet: the standard uninstall is "everything but `models/`".
- **Runtime reasons become snake_case now,** before spec 003 adds more. The page displays them
  without parsing.
- **Cache records get no format of their own.** `KEY_VERSION` already makes an old record a miss.
- **A root of the other kind,** reached through `ONEFRAME_DATA`, is used, with a warning in
  `engine.ready`.
- **Every variable that names the root's folder must be absolute** (2026-10-03, task 1).
  `ONEFRAME_DATA` and `LOCALAPPDATA` are treated as the XDG spec treats `XDG_DATA_HOME`: an empty or
  relative value is ignored, since the root functions take no working folder to resolve it against.
  On Windows, absolute means a drive and a separator, or a share (`\\server\share`).
- **The shared table is `contracts/layout.json`** (task 1). It holds the root cases and the names of
  the paths under the root, and both suites check both. The paths are cleaned (`.`, `..`, repeated
  and trailing separators) by a rule written out the same way in Python and JavaScript, because
  `ntpath` and Node's `path.win32` disagree on shares and leading double slashes.
- **Learned memory keeps `version` as the key for its format** (task 2), as it already was, so no
  migration is needed. Every other kept file uses `format`.
- **The root file holds `format`, `kind` (`dev` or `packaged`), `created` and `engine`** (task 3).
  The app passes `--packaged` when it is packaged. A root file that cannot be read, or that does not
  say its format, stops the engine as a newer layout does (`reason`: `unreadable`), since it might
  be one: nothing is overwritten that the engine does not understand. The engine's `engine.failed`
  carries `kind: root` and the reason; the app's client rejects its start with them.
- **A runtime marker without `format` is format 1, and one without `env` is never `moved`** (task
  3): markers written before this spec say neither. Installing over a `moved` environment deletes
  it first, since its scripts and links point into the root it was built in.
- **A process's folder is `cache/tmp/<n>/`, locked by `logs/locks/tmp-<n>.lock`** (task 4). It takes
  the first free number, so numbers are reused and the lock files, never deleted, stay as few as
  the processes that ever ran at once. Its runs' private folders (`runs/`), its job folders and its
  bench scratch are inside; the engine and the bench tools make it their temporary folder too.
  Anything in `cache/tmp/` that is not a numbered folder is from before this spec and is removed.
  Design's `cache/jobs/` is therefore not made: requirement 4 puts job folders in each process's
  folder, so one sweep covers both.
- **`bench:fit` writes its report to `<data>/reports/` unless `--out` names a place** (task 4), as
  `bench:runtime` does; it wrote to the working folder, the repo when run through npm.

## Out of scope

- **Packaging:** guarded deletes and `--remove-data`, one app id, a clean-VM test, and first-run
  checks for long paths and OneDrive.
- **Cache eviction**, and a size cap.
- **Splitting the child into setup and per-job code,** before spec 004.
- **A machine-readable protocol contract.**

## Owner's decisions

1. **The storage rule's exceptions** (2026-10-03). "Nothing in the repo" is kept, except a file the
   person names (such as a bench report written with `--out` into `specs/<id>/reports/`) and a dev
   checkout's engine environment (`engine/.venv`). [AGENTS.md](../../AGENTS.md) says so.
