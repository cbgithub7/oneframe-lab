# 001: Plan

Status: approved by the owner on 2026-09-29, with the six decisions below as written

The spec is [spec.md](spec.md). depth-pro-gui was read as a reference while planning; nothing in
this plan is ported or copied from it.

## Decisions for the owner

Six choices the spec leaves open. Each has the answer this plan uses. Approving the plan approves
these answers; say if one should change.

1. **Two builds installed.** `runtime_python` runs only the build the plan picks today. Another
   installed build is listed and removed with its runtime, but never run instead. For example, a
   `cpu` build installed before a GPU was fitted.
2. **Stopping an install uses a new method, `runtimes.stop`.** The spec says Stop cancels an
   install but names no method. `run.stop` stays for graph runs.
3. **Several GPUs.** The plan is made for the card with the most VRAM (the lowest index on a tie),
   and its reason says so. Choosing a card per node belongs to spec 002.
    - Added while implementing, after review, and confirmed by the owner on 2026-09-29: a node's
      child then sees only that card (`CUDA_VISIBLE_DEVICES`, in PCI order), because CUDA's own
      first card could be one the chosen build has no kernels for. Spec 002, which chooses a card
      per node, is where this pin can be lifted.
4. **Free disk can block.** A build may state `disk_mb`. If the planned build is not installed and
   the data root has less free space than that, the plan is blocked, naming both numbers.
5. **The AC9 runtime is `runtimes/torch/`:** Python 3.14 and torch only, with builds `cpu`,
   `cu126` and `cu130`. The latest torch is 2.14.0, released 2026-09-02, with cp314 wheels for
   Windows on PyPI.
    - Locking it needs `download.pytorch.org`. This cloud environment's network policy refuses that
      host (the proxy answers 403). Either add it to the environment's allowed domains, or run
      `uv lock --project runtimes/torch` on your PC and push the lock (task 9).
      **Resolved 2026-09-29:** the owner allowed `download.pytorch.org` and
      `download-r2.pytorch.org` (where the index's files are), and the runtime was locked here.
    - If torch 2.14.0 has no cu126 wheel for cp314 on Windows, the `cu126` extra pins the newest
      torch that has one. That exception goes in `versions.json` with the reason.
      **Resolved:** torch 2.14.0 has cp314 wheels for Windows and Linux on all three indexes, so
      no build is held back and there is no exception.
    - As built: driver floors (580 for cu130, 525 for cu126) and capability ranges (7.5 to 12.x,
      5.0 to 9.x) are from NVIDIA's and PyTorch's own sources, cited in its `runtime.json`.
6. **The test runtime lives with the tests,** in `engine/tests/runtimes/tiny/` rather than
   `runtimes/`, so the app never lists it. It uses Python 3.11, not the engine's 3.14. That proves
   each runtime brings its own Python, and that `child.py` runs on the oldest Python a runtime is
   likely to use. `child.py` already imports on 3.11.16 (checked).

## Files

New:

| File | Why |
| --- | --- |
| `engine/src/oneframe/hardware.py` | `profile()`: reads nvidia-smi, free disk and the OS. The parsing is pure functions the tests feed with fixtures |
| `engine/src/oneframe/runtimes.py` | See the list below the table |
| `engine/src/oneframe/runtime_install.py` | The install steps: uv, sources, stand-ins, the `.pth` file, the freeze, the marker. Runs each process so Stop kills its whole tree |
| `engine/src/oneframe/archives.py` | Downloads checked by sha256, and safe extraction of `.tar.gz` and `.zip` |
| `engine/src/oneframe/bench_runtime.py` | The AC9 report: profile, plan, install, the runtime's probe, Markdown out |
| `runtimes/torch/pyproject.toml`, `uv.lock`, `runtime.json`, `probe.py` | The torch runtime for AC9; `probe.py` is what the report runs inside it |
| `engine/tests/runtimes/tiny/pyproject.toml`, `uv.lock`, `runtime.json`, `probe.py` | The test runtime. Builds `cpu` and `cu130` pin two versions of `idna`, which the engine's lock does not have |
| `engine/tests/runtimes/tiny/stand-ins/tinyext/__init__.py` | The test runtime's one stand-in |
| `engine/tests/fixtures/nvidia-smi/*.txt` | nvidia-smi output for: one GPU, two GPUs, a driver too old for `compute_cap`, and a driver that is not loaded |
| `engine/tests/test_hardware.py` | AC2 |
| `engine/tests/test_runtime_plan.py` | AC1, definition checks, and the torch floor |
| `engine/tests/test_archives.py` | AC8 |
| `engine/tests/test_runtime_install.py` | AC3, AC5, AC7, and the marker written last |
| `engine/tests/test_runtime_server.py` | AC4, AC6, the engine methods, Stop, and one install at a time |
| `docs/runtimes.md` | Writing a runtime: the folder, `runtime.json`, builds as uv extras, extension classes, locking |

`runtimes.py` holds:

- reading, checking and discovering runtime definitions;
- `plan()`;
- status, read from the marker;
- `RuntimeMissing`;
- the `Runtimes` manager: list, plan, install, stop, remove, `python_for` and `env_for`.

Changed:

| File | Why |
| --- | --- |
| `engine/src/oneframe/__init__.py` | Adds `RUNTIMES_DIR` beside `BUILTIN_NODES_DIR` |
| `engine/src/oneframe/scheduler.py` | See the list below the table |
| `engine/src/oneframe/server.py` | Adds `--runtimes` and `--uv`, the five `runtimes.*` methods, installs on their own thread, and the rules on runs and installs |
| `app/main/main.js` | Adds the five methods to `ENGINE_METHODS`, so a later UI needs no change here |
| `package.json` | Adds `bench:runtime` |
| `docs/architecture.md` | Adds runtimes and hardware to the parts table, and the `runtime.*` events |
| `docs/nodes.md` | `run.runtime` names a folder in `runtimes/` |
| `AGENTS.md`, `README.md` | Add `runtimes/` to the layout, and the bench command |
| `docs/handoff.md` | The state, and the status of spec 001 |

`scheduler.py` changes:

- `RuntimeMissing` moves to `runtimes.py`, gains a `reason`, and is re-exported, so existing
  imports keep working.
- A `runtime_env` hook beside `runtime_python` gives a runtime's child its variables.
- `node.failed` carries the reason.

## Design

The architecture is in [docs/architecture.md](../../docs/architecture.md). This adds the part that
builds the "own uv environment" a runtime node runs in.

### A runtime in the repo

A runtime is the folder `runtimes/<id>/` (the tests pass their own root):

- **`pyproject.toml`: a virtual project** (`[tool.uv] package = false`).
    - Each build is an extra, and all of them are declared in `[tool.uv] conflicts`.
    - Each extra takes torch from its own explicit index through `[tool.uv.sources]`.
    - This is uv's documented PyTorch layout. With uv 0.12.20 in this container, I checked that two
      conflicting extras lock into one `uv.lock`, and that `uv sync --frozen --extra <build>`
      installs each build into a folder of its choosing. Nothing is written into the definition
      folder.
- **`uv.lock`:** committed, and made with `uv lock --project runtimes/<id>`.
- **`runtime.json`:**

```json
{
  "id": "tiny",
  "title": "Tiny test runtime",
  "python": "3.11",
  "builds": [
    { "name": "cu130", "vendor": "nvidia", "min_capability": "7.5", "max_capability": "12",
      "min_driver": "580", "disk_mb": 50 },
    { "name": "cu126", "vendor": "nvidia", "min_capability": "5.0", "max_capability": "12",
      "min_driver": { "windows": "528.33", "linux": "525.60.13" }, "disk_mb": 50 },
    { "name": "cpu", "vendor": "none", "disk_mb": 50 }
  ],
  "extensions": [
    { "package": "tinyext", "class": "stand-in", "for": "...", "stand_in": "stand-ins/tinyext" },
    { "package": "fastpath", "class": "optional", "for": "...", "why": "..." },
    { "package": "kernels", "class": "required", "for": "...", "needs": "a C++ compiler and the CUDA Toolkit 12.6" }
  ],
  "sources": [
    { "name": "upstream", "url": "https://codeload.github.com/<owner>/<repo>/tar.gz/<commit>",
      "sha256": "<64 hex>", "paths": ["."] }
  ],
  "env": { "SOME_FLAG": "1" },
  "probe": "probe.py:run"
}
```

How the fields read:

- Builds are listed fastest first.
- A `max_capability` of `"12"` means any 12.x.
- `min_driver` is one version, or one per OS, because NVIDIA publishes different floors for Windows
  and Linux. The torch runtime records where its numbers come from.
- The Python is set in `runtime.json`, not in `.python-version`, so the definition hash covers it.

**Checks when a definition is read.** A broken definition is listed as a problem next to the
others, the way a broken node is. The checks:

- the id matches the folder name;
- the three files exist, and `package = false` is set;
- every build is an extra, and all of them are in one conflict set;
- every `torch` in `uv.lock` is 2.6 or newer. The floor is enforced here once, not in each plan;
- each extension has a known class and that class's fields;
- each source has a sha256, and a name that is one path segment;
- `env` values are strings;
- the probe file exists.

Nothing in the engine names a runtime. The manager finds `*/runtime.json` under its roots.

### A runtime on this machine

```
<data>/runtimes/<id>/<build>/                         the environment (UV_PROJECT_ENVIRONMENT)
<data>/runtimes/<id>/<build>/src/<name>/              pinned sources
<data>/runtimes/<id>/<build>/stand-ins/               stand-ins
<data>/runtimes/<id>/<build>/oneframe-runtime.json    the marker, written last
<data>/runtimes/<id>/downloads/                       source archives, by sha256
<data>/uv/cache/, <data>/uv/python/                   uv's cache and Pythons, where the app already puts them
```

Everything that belongs to a runtime is under `<data>/runtimes/<id>/`, so removing it is one folder.

### The machine profile

`hardware.profile()` runs this command, with a 10 s timeout:

```
nvidia-smi --query-gpu=index,name,compute_cap,memory.total,memory.free,driver_version --format=csv,noheader,nounits
```

- **Where nvidia-smi is looked for:** on PATH and, on Windows, also in
  `%ProgramFiles%\NVIDIA Corporation\NVSMI`.
- **Older drivers:** a driver too old to know `compute_cap` is asked again without it, and the
  capability is reported as unknown.
- **No card:** a missing command, a non-zero exit (the driver is not loaded) or a timeout gives
  `gpus: []` with the reason, never an exception.
- **Units:** nvidia-smi reports MiB. They are converted to MB, like `peak_vram_mb`.
- **Disk:** free disk is `shutil.disk_usage` on the data root.
- **Output:** `os`, `gpus` (index, vendor, name, capability, total and free VRAM), `driver`,
  `nvidia` (found, why), `disk_free_mb`, and `raw`. `raw` is the output as nvidia-smi printed it,
  for the report.
- **Names:** kept for display only. `plan()` never reads them.
- **When it is read:** once, on first use, and again when `runtimes.plan` is asked to refresh.

### Plan

`plan(runtime, profile, installed=())` is pure: it reads no files and starts no processes.
It works through these steps:

1. **The card:** the NVIDIA GPU with the most VRAM, or none.
2. **Each GPU build, in order.** A build is refused, with the reason, when:
    - there is no card of its vendor;
    - the driver is older than its floor. This is checked first, so a driver problem is named as
      one;
    - the capability is unknown, or outside the build's range;
    - the OS is not one it lists.

   The first build that passes is chosen.
3. **Otherwise the `cpu` build,** if there is one. The GPU builds' reasons stay in `considered`.
4. **Otherwise blocked:** "No build of <title> runs here: " followed by each build's reason.
5. **Required extensions block.** A `required` extension blocks any chosen build (the owner's
   decision 3 in the spec). The reason names the package, what it is for, and what building it
   needs. `optional` extensions are noted as left out.
6. **Disk,** as in decision 4 above.

The result:

```
{ runtime, build | null, why, blocked | null, considered: [{ build, chosen, why }], notes }
```

Versions compare as integer tuples, so 12.10 is above 12.9 and 560.94 is above 528.33.

### Install

`runtimes.install {runtime, build?}` answers at once with `{runtime, build}`, then works on a thread
of its own.

- The build defaults to the plan's. A build the plan refused is refused, with the plan's reason.
- A runtime that is installed and current answers `already: true`, and nothing runs.

The steps, each announced as `runtime.step` with `index` and `total`:

1. **Delete the marker,** if there is one. From here the runtime counts as not installed until the
   last step. A rebuild that is killed therefore never leaves an old marker vouching for a new
   environment.
2. **`uv python install <python>`.**
3. **`uv sync`:**

   ```
   uv sync --frozen --no-config --no-dev --managed-python --compile-bytecode --extra <build> --python <python> --project <definition>
   ```

    - Environment: `UV_PROJECT_ENVIRONMENT`, `UV_CACHE_DIR` and `UV_PYTHON_INSTALL_DIR`; with
      `--managed-python`, a system Python is never used.
    - uv's stderr lines are forwarded as `runtime.log` events.
    - `--no-config` keeps a person's own `uv.toml` out of the build.
    - `--compile-bytecode`: the install compiles every module once, so a node's first run does not
      pay for it.
    - Resuming: `uv sync` over a half-built folder finishes it, which is what makes an install
      resumable. A folder uv cannot use is started again.
4. **Sources.**
    - Each archive downloads to `downloads/<sha256>.part`, with `runtime.progress` events counting
      bytes.
    - The sha256 is checked, then the file is renamed into place. An archive already there with the
      right hash is not fetched again.
    - It is extracted into a temporary folder beside `src/`. Every member is checked (AC8), then
      the folder is renamed into place.
5. **Stand-ins,** copied from the definition.
6. **`oneframe-runtime.pth`,** written into the environment's site-packages, which its own Python
   reports. It lists the source paths and the stand-ins folder, which makes them importable.
7. **The freeze and the size:** `uv pip freeze --python <env python>`, and the folder's size.
8. **The marker,** written to a temporary name and then renamed. Its fields: runtime, build,
   `lock_sha256`, `definition_sha256`, python, uv, freeze, sources, stand-ins, left out,
   `size_bytes`, `installed_at`.

**Events around the steps:** `runtime.start` comes first. The last event is one of `runtime.done`
(with the python, size and seconds), `runtime.failed` (the message and uv's last lines) or
`runtime.stopped`.

**Stop.** `runtimes.stop {runtime}` sets the install's stop flag.

- The running step's process tree is killed, because uv starts Python workers for bytecode. On
  POSIX the step runs in a new process group; on Windows it is killed with `taskkill /T /F`.
- The download loop checks the flag between chunks.
- The marker is never written.

**Limits.** One install runs at a time, engine-wide. These are refused:

- installing or removing a runtime that the running graph uses;
- removing the runtime that is being installed.

### Status, and what the scheduler gets

A runtime's status on this machine is one of:

- **`installing`.**
- **`installed`:** the planned build's marker exists, its hashes match the current `uv.lock` and
  `runtime.json`, and its interpreter exists. `.gitattributes` makes both files LF, so the hashes
  are the same on Windows and Linux.
- **`out of date`:** the marker's hashes differ from the current files.
- **`not installed`:** anything else, including a folder left by an interrupted install.

`runtimes.list` gives, for each runtime:

- its status;
- its build: the installed one, else the planned one;
- its size, read from the marker, so listing never walks gigabytes;
- its plan;
- other builds found on disk.

The definition problems are listed too.

**The scheduler's hooks:**

- **`Runtimes.python_for(id)`** is the scheduler's `runtime_python`. It returns the interpreter of
  the planned build when that is installed. Otherwise it raises `RuntimeMissing` with a sentence a
  person can act on, and a `reason`: `unknown`, `blocked`, `installing`, `not installed` or
  `out of date`.
- **`Runtimes.env_for(id)`** is the new `runtime_env` hook. It returns the `env` from
  `runtime.json`, which is passed to `ProcessExecutor`. `child_env` already strips the engine's own
  Python variables before adding these.

### Engine methods

| Method | Answer |
| --- | --- |
| `runtimes.list` | `{runtimes, problems, profile}` |
| `runtimes.plan {runtime?, refresh?}` | The plan for one runtime or all of them, and the profile used |
| `runtimes.install {runtime, build?}` | `{runtime, build}` at once; progress arrives as `runtime.*` events |
| `runtimes.stop {runtime}` | `{stopping}` |
| `runtimes.remove {runtime}` | `{removed}` |

Before `runtimes.remove` deletes `<data>/runtimes/<id>/`, it checks that the path resolves to a
direct child of `<data>/runtimes` named for a known runtime id. Read-only files are handled on
Windows.

uv is found in this order:

1. `--uv`;
2. the `UV` variable, which `uv run` sets for the engine (checked);
3. `ONEFRAME_UV`;
4. PATH.

With no uv, install fails and says so; list and plan still work. List and plan never start any
process except nvidia-smi, and never open a socket (AC6).

### The report (AC9)

`npm run bench:runtime -- <id> [--out <file>]` runs:

```
uv run --project engine --frozen python -m oneframe.bench_runtime
```

It reads the profile, plans, installs (timed), and runs the runtime's `probe` through
`ProcessExecutor` with the network closed. The report is Markdown, and holds:

- the date and the OS;
- the raw nvidia-smi output;
- the plan, with every build considered;
- the install's seconds and size;
- the torch lines of the freeze;
- what the probe returned.

`runtimes/torch/probe.py` returns:

- torch's version and its CUDA version;
- `torch.cuda.is_available()`;
- the device's capability;
- the seconds and peak VRAM for a 4096 × 4096 matmul.

Nothing in the engine imports torch. The probe runs only inside the runtime. The report goes to
`<data>/reports/` unless `--out` says otherwise.

## Risks

- **uv changes how `--frozen` treats conflicting extras.** uv is pinned in CI (0.12.20), and the
  engine requires at least that. The integration tests run the real uv on Windows and Linux.
- **The integration tests need the network:** PyPI, and GitHub for uv's Python 3.11 download. CI
  has both. Offline, these tests fail with the download error rather than skipping, so a skipped
  test never passes for a real one.
- **PyTorch stops publishing cu126** for a later release. The `cu126` extra has its own torch pin
  in the same lock, so it can stay behind while `cu130` moves on.
  `npm run versions` does not read runtime locks yet (see below).
- **GitHub's archive downloads are not promised to be byte-stable.** A changed archive fails its
  sha256 check, and the install is refused with both hashes named (AC8). The fix is a new pin in
  `runtime.json`.
- **A killed uv holds its cache lock** until its processes are gone. The tree kill ends them; if
  one survives, the next install waits for it, then resumes.
- **Sizes and hard links.** uv hard-links files from its cache into environments on the same
  volume. Keeping the cache under the data root keeps them on one volume. The reported size is the
  folder's size, and `docs/runtimes.md` says so.
- **Remove deletes gigabytes.** The path check guards it, and AC7's test shows that neighbouring
  folders survive. The app waits 60 s for an answer, which a large runtime on a slow disk may
  exceed. If that happens, remove moves to a thread like install.
- **nvidia-smi fixtures.** They are written from nvidia-smi's documented CSV format, not recorded
  on the owner's PC; there is no GPU here. The AC9 report carries the raw output, and task 11 adds
  it as a fixture.
- **Python versions.** `child.py` must keep running on every runtime's Python. The tiny runtime
  runs it on 3.11 in CI.

## Not in this plan

- **`npm run versions` reading runtime locks.** The torch pin is checked by hand for now, and the
  handoff lists this as a follow-up.
- **Any UI for runtimes;** that is the workspace UI spec. The methods are in `ENGINE_METHODS`, so
  the UI can be added without touching `app/main/main.js`.

## Tests

- Added:
    - `test_hardware.py` (AC2):
        - one GPU, and two GPUs;
        - a driver without `compute_cap`, and a driver that is not loaded;
        - the command not found, and a timeout.
    - `test_runtime_plan.py`:
        - the five AC1 cases (AC1);
        - a refused GPU build says whether the capability or the driver refused it;
        - "12.10" is above "12.9";
        - per-OS driver floors;
        - a required extension blocks the plan, naming the package and what it needs;
        - an optional extension is noted as left out;
        - disk can block;
        - a GPU's name appears in no plan (AC2);
        - each kind of definition problem is listed without hiding the other runtimes;
        - a lock holding torch 2.5.1 is refused.
    - `test_archives.py` (AC8):
        - a wrong sha256 is refused, and nothing is kept;
        - tar members that escape by `..`, by an absolute path, by a symlink or by a hard link are
          refused;
        - zip members that escape by `..` or by a drive letter are refused;
        - a good archive extracts.
    - `test_runtime_install.py`:
        - the tiny runtime installs under the data root, with Python 3.11, its build's `idna`, and
          its source and stand-in importable;
        - the marker is written last;
        - a runtime node then runs in it through the scheduler, with its env variable set and the
          network closed (AC3);
        - an NVIDIA profile picks `cu130`, which installs the other `idna` from the same lock;
        - after `uv.lock` or `runtime.json` is edited, the runtime is out of date and `python_for`
          refuses it (AC5);
        - remove deletes only that runtime's folder (AC7);
        - a failure before the marker leaves no marker.
    - `test_runtime_server.py`:
        - each method works over the NDJSON server;
        - the engine is killed during a source download: the runtime is not installed, and a second
          install completes (AC4);
        - Stop at the same point gives `runtime.stopped` and no marker;
        - a second install is refused while one runs;
        - list and plan run with sockets blocked and process starts watched: only nvidia-smi
          starts, and nothing connects (AC6);
        - the bench, run against the tiny runtime, writes a report holding the probe's answer.
    - Every runtime in `runtimes/` reads without problems.
- Removed: none

The AC4 kill is made deterministic without a test hook. For that test, the tiny runtime's source is
served from 127.0.0.1 by a server that sends half the file and waits. The test kills the engine
once the first `runtime.progress` for it arrives. At that point `uv sync` is done, and the marker
cannot have been written yet.

## Verification

| Criterion | Checked by |
| --- | --- |
| AC1 | `test_runtime_plan.py`: `test_the_plan_picks_the_build_each_machine_can_run` (the five cases), `test_a_runtime_without_a_cpu_build_is_blocked_by_an_old_driver_and_says_so` |
| AC2 | `test_hardware.py`: `test_one_gpu_is_read_from_nvidia_smi`, `test_two_gpus_are_read_from_nvidia_smi`, `test_no_nvidia_smi_means_no_gpu_not_an_error`; `test_runtime_plan.py`: `test_no_gpu_name_reaches_a_plan` |
| AC3 | `test_runtime_install.py`: `test_a_runtime_installs_from_its_lock_and_runs_a_node`, in CI on windows-latest and ubuntu-latest |
| AC4 | `test_runtime_server.py`: `test_a_killed_install_is_not_installed_and_a_second_install_completes` |
| AC5 | `test_runtime_install.py`: `test_a_changed_lock_makes_a_runtime_out_of_date` |
| AC6 | `test_runtime_server.py`: `test_listing_and_planning_touch_no_network` |
| AC7 | `test_runtime_install.py`: `test_remove_deletes_only_that_runtime` |
| AC8 | `test_archives.py`: `test_a_source_with_the_wrong_sha256_is_refused`, `test_archive_members_that_escape_are_refused` |
| AC9 | The owner's report, below |

AC9, on the owner's Windows PC with the GTX 1070, from a checkout of the PR's branch:

```
npm ci
npm run engine:sync
npm run bench:runtime -- torch --out specs/001-runtime-manager/reports/<yyyy-mm-dd>-gtx1070.md
```

This downloads torch's cu126 build, a few GB. The report passes if it shows:

- the plan chose `cu126`, with `cu130` refused for compute 6.1;
- `torch.cuda.is_available()` is true, and the capability is (6, 1);
- the install time and size;
- the matmul's seconds and peak VRAM.

Commit the report under `specs/001-runtime-manager/reports/`. Until then the PR lists AC9 as
unverified.
