# 001: Runtime manager

Status: done (every acceptance criterion verified 2026-09-30; AC9 by the owner's GTX 1070 report)
Owner approval: 2026-09-29 (spec and plan)

## Problem

A node whose code imports a model library runs in its family's own Python environment (a
"runtime"), so families with conflicting torch, CUDA and extension versions never meet. The
scheduler already runs such nodes in a child process given a Python, but nothing builds, checks,
records or removes these environments. Until something does, no model node can run.

## Requirements

1. **Runtimes are data.** A runtime is a folder `runtimes/<id>/` in the repo:
    - `pyproject.toml` and a committed `uv.lock`;
    - `runtime.json`: its builds, its compiled extensions and their class, pinned upstream
      sources, and environment variables.

    Nothing in the engine names a runtime.
2. **One lock, several builds.** A runtime can offer several builds of the same packages, for
   example `cpu`, `cu126` and `cu130`, expressed as uv extras with conflicting indexes in one
   `uv.lock`. Each build states the GPU vendor, the lowest and highest compute capability, and
   the lowest driver it needs.
3. **Choosing a build is deterministic.** Given a machine profile (GPU vendor, compute
   capability, VRAM, driver version, OS, free disk), `plan(runtime, profile)` returns:
    - the build to use and why, or that the runtime is blocked on this machine and why;
    - a list of the builds it considered, each with the reason it was not chosen.

    Order: the fastest build the machine supports, then CPU if the runtime has one, else
    blocked. Every torch build is at least 2.6.
4. **A machine profile the engine can read.** `hardware.profile()` reads NVIDIA GPUs with
   `nvidia-smi` (name for display only, compute capability, total and free VRAM, driver), free
   disk on the data root, and the OS. It reports "no NVIDIA GPU" rather than failing when
   `nvidia-smi` is missing. It never keys on GPU names.
5. **Install is explicit, resumable and honest.**
    - `install(runtime, build)`:
        - runs `uv sync --frozen` for that build into `<data>/runtimes/<id>/<build>/`, with uv's
          cache and Pythons under the data root;
        - fetches pinned upstream sources (sha256-checked) and makes them importable;
        - applies each compiled extension's class (stand-in, optional, required).
    - It streams progress as events.
    - A runtime counts as installed only once a marker is written last, holding the lock hash,
      the build and a package freeze. An interrupted install leaves no marker and is resumed or
      redone.
6. **Rebuild only on change.** An installed runtime whose `uv.lock` or `runtime.json` has changed
   since its marker is reported "out of date" and rebuilt only when asked.
7. **The scheduler uses it.** `runtime_python(id)` returns the installed build's interpreter, or
   raises `RuntimeMissing` with the reason: not installed, out of date, or blocked on this
   machine.
8. **Engine methods:**
    - `runtimes.list`: each runtime with its status, build, size and plan;
    - `runtimes.plan`: the plan for this machine;
    - `runtimes.install`: answers at once; progress arrives as `runtime.*` events; Stop cancels it;
    - `runtimes.remove`: deletes that runtime's folder only.
9. **Nothing downloads unless asked.** Listing and planning never touch the network. Only
   `runtimes.install` does.

## Acceptance criteria

- [ ] AC1: For a test runtime offering `cpu`, `cu126` (compute 5.0–12.x, driver ≥ 528) and
  `cu130` (compute 7.5+, driver ≥ 580), `plan` picks:
    - `cu126` for compute 6.1 on driver 560;
    - `cu130` for compute 8.6 on driver 580;
    - `cu126` for compute 8.6 on driver 560;
    - `cpu` with no NVIDIA GPU;
    - blocked, with a reason naming the driver, for a runtime with no CPU build on driver 470.

  Checked by unit tests.
- [ ] AC2: `hardware.profile()` parses recorded `nvidia-smi` output for one GPU, two GPUs and
  "command not found" (unit tests with fixtures). No GPU name appears in any decision.
- [ ] AC3: Installing a tiny test runtime from its committed lock builds an environment under
  the data root, writes the marker last, and a `runtime` node then runs in it through the
  scheduler with the network closed (integration test in CI, Windows and Linux).
- [ ] AC4: Killing an install midway leaves no marker; `runtimes.list` shows it as not installed,
  and a second install completes (integration test).
- [ ] AC5: Changing the test runtime's lock after install makes `runtimes.list` report
  "out of date", and `runtime_python` refuses it with that reason (integration test).
- [ ] AC6: `runtimes.list` and `runtimes.plan` make no network connection (test with sockets
  blocked).
- [ ] AC7: `runtimes.remove` deletes only `<data>/runtimes/<id>/` (test).
- [ ] AC8: A pinned upstream source whose sha256 does not match is refused, and so is an archive
  member that escapes the target folder, by path or by link (unit tests).
- [ ] AC9 (hardware): On the owner's Windows PC (GTX 1070, compute 6.1), installing a torch
  runtime picks `cu126`, and inside it `torch.cuda.is_available()` is true. Proven by the report
  from `npm run bench:runtime -- <id>`, committed under `specs/001-runtime-manager/reports/`.

## Out of scope

- The attempt ladder and allocator ceiling per node (spec 002).
- Downloading model weights (spec 003).
- Any real model runtime beyond the one AC9 needs.
- Runtimes inside WSL (a later spec, only if a chosen node needs Linux).
- AMD and Intel builds: the plan format allows them, but none ships now.
- Bundling uv with an installer (the packaging spec).

## Decisions

Answered by the owner on 2026-09-29, when the spec was approved:

1. **Locks:** one `uv.lock` per runtime, with one build option (uv extra) per torch build:
   `cpu`, `cu126` and `cu130`.
2. **Where runtimes live:** a top-level `runtimes/` folder, shared by node families.
3. **Extensions that need a compiler:** the runtime is blocked on this machine, with a reason that
   names the extension and what it needs. Prebuilt wheels are added per runtime later, when a
   chosen node needs one.
