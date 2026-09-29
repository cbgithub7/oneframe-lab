# 001: Tasks

Status: in progress (plan approved 2026-09-29)

Each task leaves `npm run check` and `npm run engine:check` green. Tick each one as it lands. The
design each task follows is in [plan.md](plan.md).

- [x] 1. **Machine profile.**
    - `oneframe/hardware.py`: `profile()`, and pure parsers for nvidia-smi's CSV.
    - Fixtures in `engine/tests/fixtures/nvidia-smi/`.
    - `test_hardware.py`: one GPU, two GPUs, a driver without `compute_cap`, a driver not loaded,
      the command not found, a timeout. (AC2, except the name check, which needs task 3.)
- [x] 2. **Runtime definitions.**
    - `RUNTIMES_DIR` in `oneframe/__init__.py`.
    - In `oneframe/runtimes.py`: read, check and discover `runtime.json` with its `pyproject.toml`
      and `uv.lock`. A broken one is a problem that does not hide the others.
    - The tiny test runtime in `engine/tests/runtimes/tiny/`, locked with
      `uv lock --project engine/tests/runtimes/tiny`.
    - Tests for each definition check, including the torch floor (a lock with torch 2.5.1).
    - The first version of `docs/runtimes.md`: the folder and `runtime.json`.
- [x] 3. **Plan.**
    - `plan()` in `runtimes.py`: builds in order, driver before capability, CPU after, then
      blocked; extension classes; disk.
    - `test_runtime_plan.py`: the five AC1 cases, the blocked reason naming the driver, "12.10"
      above "12.9", per-OS driver floors, a required extension blocks, an optional one is noted, disk
      blocks, no GPU name in any plan. (AC1, and the rest of AC2.)
- [x] 4. **Archives.**
    - `oneframe/archives.py`: a download to `.part`, checked by sha256, then renamed; extraction that
      refuses members escaping by path, absolute path, symlink, hard link or drive letter.
    - `test_archives.py`. (AC8)
- [ ] 5. **Install, status and remove.**
    - `oneframe/runtime_install.py`: the eight steps and their events, and Stop killing the step's
      process tree.
    - `Runtimes` in `runtimes.py`: status from the marker, `python_for`, `env_for`, `remove`.
    - `test_runtime_install.py`:
        - the tiny runtime installs under the data root, with Python 3.11, `idna`, and its source
          and stand-in importable;
        - the marker is written last, and a failure before it leaves none;
        - an NVIDIA profile installs the `cu130` build's `idna`;
        - out of date after the lock or `runtime.json` changes (AC5);
        - remove deletes only that folder (AC7).
    - `docs/runtimes.md`: builds as uv extras, extension classes, locking, sizes and hard links.
- [ ] 6. **The scheduler.**
    - `RuntimeMissing` moves to `runtimes.py` with its `reason`, and is re-exported from
      `scheduler.py`.
    - The `runtime_env` hook, passed on to `ProcessExecutor`; `node.failed` carries the reason.
    - Test: a runtime node runs in the installed tiny runtime through the scheduler, with its env
      variable set and the network closed. (AC3)
    - `docs/architecture.md` (the parts table) and `docs/nodes.md` (`run.runtime`).
- [ ] 7. **Engine methods.**
    - In `server.py`: `runtimes.list`, `runtimes.plan`, `runtimes.install`, `runtimes.stop`,
      `runtimes.remove`; `--runtimes` and `--uv`; installs on a thread, one at a time; the rules on
      runs and installs.
    - The five methods in `ENGINE_METHODS` in `app/main/main.js`.
    - `test_runtime_server.py`:
        - the engine killed during a source download, then a second install (AC4);
        - Stop, and a second install refused while one runs;
        - list and plan with sockets blocked and process starts watched (AC6).
    - The `runtime.*` events in `docs/architecture.md`.
- [ ] 8. **The report.**
    - `oneframe/bench_runtime.py` and the `bench:runtime` script in `package.json`.
    - The tiny runtime's `probe.py`.
    - A test that runs the bench against the tiny runtime and finds the probe's answer in the
      report.
    - The command in `AGENTS.md` and `README.md`.
- [ ] 9. **The torch runtime.**
    - `runtimes/torch/`: Python 3.14, torch 2.14.0, builds `cu130`, `cu126` and `cpu`, and
      `probe.py`.
    - Driver floors from NVIDIA's CUDA release notes, with the source recorded in `runtime.json`.
    - The lock, which needs `download.pytorch.org`. Either the owner adds that host to this cloud
      environment's allowed domains, or runs `uv lock --project runtimes/torch` on their PC and
      pushes the lock (plan, decision 5).
    - A cu126 pin behind 2.14.0, if one is needed, goes in `versions.json` with its reason.
    - Test: every runtime in `runtimes/` reads without problems.
    - `runtimes/` in the layout in `AGENTS.md` and `README.md`.

    Lands only with its lock, because the definition check needs it. Tasks 8 and 10 do not wait
    for it.
- [ ] 10. **Wrap-up.**
    - `docs/handoff.md`: the state, the spec's status, and the follow-up to make `npm run versions`
      read runtime locks.
    - Run `/code-review` on the branch and fix what it finds.
    - Open the PR, with each criterion ticked by its test name, and AC9 listed as unverified.
- [ ] 11. **The owner, on the GTX 1070: AC9.**
    - Run `npm run bench:runtime -- torch --out specs/001-runtime-manager/reports/<date>-gtx1070.md`,
      then commit the report.
    - Add its raw nvidia-smi output to `engine/tests/fixtures/nvidia-smi/`, with a test that it
      parses.
