# 006: Foundations

Status: draft
Owner approval: (date, once approved)

The first spec under the one-document process. It comes before spec 003's core, because the model
store builds on each piece here ([the review of 2026-10-02](../../docs/reviews/2026-10-02.md),
sections 4, 5 and 8B).

## Problem

What the app writes, and how it fails, is not yet something a person can trust:

- **It writes outside its data root.** Electron's caches and crash dumps go to Roaming. Job folders
  go to the system temp folder, bytecode goes into `nodes/`, and model libraries will write their
  caches to the home folder.
- **Two processes can damage one root.** Engine start empties `cache/tmp` under another engine's
  feet, and an install lock holds only within one process.
- **No file says which format it is in.** A newer file can be overwritten by an older app, and a
  root that was moved still reports its runtimes installed.
- **Two places compute the data root,** and they disagree.
- **Failures come in four shapes,** and a person cannot hand over one file that explains one.
- **The cache can serve stale results:** it serves results from before a node's code or its
  runtime changed.

## Requirements

1. **One root, defined once.** One module in the engine and one in the app compute the data root,
   and a shared table of cases (platforms, `ONEFRAME_DATA`, `LOCALAPPDATA`, `XDG_DATA_HOME`)
   holds them equal. A dev checkout and a packaged app never share a root: a checkout uses
   `OneframeLab-dev` (`oneframe-lab-dev` on Linux). A relative `XDG_DATA_HOME` is ignored, as XDG
   says.
2. **Nothing is written outside the root.** Everything the app, the engine, a runtime child or a
   command-line tool writes lands under it:
    - Electron's user data, session data and crash dumps;
    - job folders, which were in the system temp folder;
    - Python bytecode for nodes and children;
    - the caches model libraries keep: the hub's home, torch and its compiler caches, Triton, the
      CUDA kernel cache, and the general cache folder libraries fall back to.

   The plan is in Design. The single-instance lock is then per root.
3. **Every file the app keeps says its format,** and a root says its own. `oneframe-root.json` holds
   the layout's format, the root's id, and whether a dev checkout or a packaged app made it.
    - The formats covered: settings, learned memory, runtime markers and cache records.
    - A file in a newer format than this app reads is left alone. Whatever needs it then says why,
      and it is never overwritten.
    - A runtime marker records the environment's own path. A runtime whose root moved reads as
      "moved: install again", not as installed.
4. **One process changes a root at a time where it matters.** The engine that holds the root's lock
   is the only one that sweeps temporary folders at start. Installing a runtime takes a lock for
   that runtime that holds across processes. Locks are released by the operating system when a
   process dies, so a crash never leaves a root locked.
5. **One failure model.**
    - Every failure the engine reports carries a `kind`, a snake_case `reason` where it has one, a
      `message` for a person, and `next` (what to do) where it is known. This covers node
      failures, runtime install failures and error replies.
    - Runtime reasons become snake_case: `not_installed`, `out_of_date`, `installing`, `blocked`,
      `unknown`, `moved`.
    - There is one `Stopped`.
    - [docs/architecture.md](../../docs/architecture.md) lists every kind and reason, and a test
      holds the list to the code.
6. **Failures leave evidence.**
    - Every event of a run and of an install is appended to a journal under `logs/`.
    - Journals and per-step logs are pruned to a bound the plan sets.
    - `npm run diagnose` writes one file a person can hand over. It holds:
        - the app's, the engine's and each runtime's versions;
        - the machine profile, with the raw nvidia-smi output;
        - each runtime's marker and packages;
        - the settings;
        - what this machine learned;
        - the latest journals and logs.

      The home folder and the user name are replaced, and no token or secret is included.
7. **Results follow code.** A node's cache key includes a hash of its folder's files and, for a
   runtime node, the hash of its runtime's lock. Editing a node or upgrading its runtime runs it
   again; nothing else does.
8. **The rules say so.** [AGENTS.md](../../AGENTS.md) gains the storage rules (decision 10): one
   root, versioned formats, safe deletes, separate dev and packaged roots.

## Acceptance criteria

- [ ] AC1: The shared table gives the same root from Python and JavaScript for every case,
  including `ONEFRAME_DATA`, a missing `LOCALAPPDATA` and a relative `XDG_DATA_HOME`. A dev and a
  packaged root differ (tests in both suites).
- [ ] AC2: **Footprint**, in CI on Windows and Linux. With the home folder, the app data folders
  and temp pointed into a sandbox:
    - install the tiny runtime;
    - run a graph with a runtime node;
    - start and stop the engine.

  Afterwards nothing exists outside the root, and on Windows no new `HKCU\Software\Python` key
  exists. The child's environment points every library cache of requirement 2 under the root.
- [ ] AC3: Electron's paths are computed under the root before the single-instance lock, by a
  function `main.js` calls (unit test).
- [ ] AC4: Formats (tests):
    - each covered file carries its format;
    - a newer-format settings, learned or marker file is left untouched, with a message that says
      why;
    - a runtime environment moved to another root reads as `moved`.
- [ ] AC5: Two engine processes on one root:
    - only the lock holder sweeps temporary folders;
    - a second install of the same runtime is refused with reason `locked` while the first runs;
    - killing the first frees the lock (tests with subprocesses).
- [ ] AC6: A test lists every failure kind and reason the engine can emit, and checks each is
  described in the architecture doc. Error replies carry `kind` and `reason`.
- [ ] AC7: Diagnostics:
    - a run's events are in its journal, in order;
    - pruning keeps the bound;
    - `npm run diagnose` writes one file with each section of requirement 6, and the file contains
      neither the home folder's path nor an `HF_TOKEN` set in the environment (tests).
- [ ] AC8: Changing a file in a node's folder, or its runtime's lock, changes its cache key. The
  same code and lock give the same key (unit tests).

## Design

- **`oneframe/layout.py`** names every path under the root, and nothing else in the engine builds
  one. `app/main/paths.js` mirrors it for the app's part. The shared table is a JSON file read by
  both test suites. A runtime child gets its library cache variables from the same module, set in
  `child_env` with the forced variables:
    - `HF_HOME`, `TORCH_HOME`, `TORCHINDUCTOR_CACHE_DIR`, `TRITON_CACHE_DIR`, `CUDA_CACHE_PATH`,
      `XDG_CACHE_HOME` and `MPLCONFIGDIR`, under `cache/runtime/<id>/`;
    - `TMP` and `TEMP`, under `cache/jobs/`;
    - `PYTHONPYCACHEPREFIX` for nodes and children.

  `cache/` gets a `CACHEDIR.TAG`, so backup tools skip it.
- **The layout keeps today's top-level folders:** `settings.json`, `memory/`, `logs/`, `cache/`,
  `runtimes/`, `uv/` and `reports/`. It adds:
    - `oneframe-root.json`;
    - `electron/` for user data;
    - `cache/electron/` for session data;
    - `logs/crashes/`;
    - `cache/jobs/` and `cache/runtime/`.

  A root without `oneframe-root.json` is format 0, and the engine writes it on first start. No file
  moves, so no migration is needed today. Spec 003's store lands in `models/`, which the standard
  uninstall keeps.
- **One JSON writer** writes to a unique temporary name, flushes, and replaces the file. It never
  writes over a newer format. Locks use the operating system's file lock (`msvcrt` or `fcntl`) on
  files under `logs/locks/`.
- **`oneframe/errors.py`** (begun with `ContractError`) holds the failure type that every subsystem
  raises: kind, reason, message, next and retry.
- **The journal is written by the server,** which already sees every event, one file per run or
  install.

## Tests

- Added: the shared root table (both suites); footprint (Windows and Linux CI); Electron paths;
  formats and moved runtimes; two-process locks; the failure-kind inventory; journal, pruning and
  diagnose; cache keys from code and lock.
- Removed: none.

## Verification

Every criterion runs in CI on Windows and Linux; none needs a GPU. After the merge, the owner's PC
starts with an empty dev root, `OneframeLab-dev`. Its runtimes are installed again, the torch
runtime with `npm run bench:runtime -- torch`. The old `OneframeLab` folder can be deleted by hand.

## Decisions taken

- **The dev root is renamed now, not moved.** The only cost is reinstalling the torch runtime once
  on the owner's PC. It needs no migration code, and it keeps the clean name for the packaged app.
- **The top-level layout stays as it is.** Regrouping into config, state and cache folders would
  need a migration and buy nothing yet: the standard uninstall is "everything but `models/`".
- **Runtime reasons become snake_case now,** before spec 003 adds more. The page shows them
  without parsing, so nothing depends on the spaced form.

## Out of scope

- **Packaging:**
    - the uninstaller's guarded deletes and `--remove-data`;
    - one app id;
    - the clean-VM test;
    - the first-run checks for long paths and OneDrive.
- **A size cap and eviction** for the node cache, a later spec.
- **Splitting the child into setup and per-job code,** and an executor provider, before spec 004.
- **A machine-readable protocol contract,** with spec 004 or the UI.
- **The store's own location setting and pointer file** (spec 003's later part).
