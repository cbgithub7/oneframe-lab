# Handoff: where the project is and what comes next

Read this first in a new session, then [architecture.md](architecture.md) and [nodes.md](nodes.md).
The wider proposal (why this design, the survey of about 50 models, the roadmap) is the doc
"Oneframe Lab: foundation proposal":
https://claude.ai/code/artifact/9df2417a-7122-415a-8b5d-f65daf5bc123

## Decisions already made

- **What it is.** A personal, free, non-commercial, local-only playground for making 3D content
  from a single photo. Non-commercial model licences are fine; licence data is shown, not used
  to block.
- **Two loops.**
    - *Generate:* photo → a depth scene, a mesh, a splat, or segmented objects placed in a scene.
    - *Improve:* photo, or a first result rendered along a camera path → synthesised views →
      reconstruction → a better result, compared against the first.
- **Everything is a node.** A node is a manifest plus its code. Ports are typed, with facets.
  Nothing outside a node's folder names a model.
- **Local only.** No hosted models, no accounts, no network during a run. Weights download only
  when a person presses Download.
- **Versions.** The latest stable release everywhere, with exact pins. An exception goes in
  `versions.json` with a reason and a review date ([versions.md](versions.md)). Check the
  latest release before adding any dependency.
- **A separate project.** `cbgithub7/depth-pro-gui`, the owner's earlier app, may be read as a
  reference for what it learned. Nothing is ported or copied from it, and nothing here has to
  match it.
- **Honesty.** Nothing is called tested or working without numbers from a real run. The cloud
  container has no GPU and cannot reach Hugging Face; real model runs happen on the owner's PC.
  The owner's test machine is a GTX 1070 (8 GB, compute 6.1). It is the only card we can test
  on, never the design target: the app runs on any machine that can run a model, from no GPU to
  the largest cards, and fails with a clear reason when it cannot. Nothing is tuned to the 1070
  (the "Any card that can run it" rule in [AGENTS.md](../AGENTS.md)).

## State (2026-10-02)

Done and green (local, and GitHub Actions on Windows and Ubuntu):

- **Engine** (`engine/src/oneframe/`):
    - port types with facets, manifests and the registry;
    - graph planning with automatic converters;
    - the content-addressed cache and the scheduler, with trust carried through;
    - engine and runtime-child executors, and the child protocol;
    - the NDJSON stdio server.
- **Runtime manager** (spec 001, merged in PR #3):
    - runtime definitions in `runtimes/`, found by looking ([runtimes.md](runtimes.md));
    - the machine profile from nvidia-smi, and the plan that picks a build by capability and
      driver, never by a card's name;
    - install with uv from the committed lock, resumable, with the marker written last;
      out-of-date detection; remove;
    - the `runtimes.*` engine methods and `runtime.*` events;
    - `npm run bench:runtime`, the report a hardware claim needs.

    Tested in CI with a small test runtime (`engine/tests/runtimes/tiny/`). The first real
    runtime is `runtimes/torch/` (torch 2.14.0; cpu, cu126, cu130); its cpu build installed and
    ran its probe in a cloud session, and its cu126 build passed AC9 on the owner's GTX 1070.
    Locking a runtime needs `download.pytorch.org` and `download-r2.pytorch.org` reachable.
- **Fit to memory** (spec 002, done, merged in PR #5):
    - a node's memory model in its manifest, checked with every problem named, and the
      `precision` param the engine adds from it;
    - the fit before each load: free memory less a margin, upgrades, speed-only changes before
      quality, never a setting the graph sets, the slow-device warning with the faster
      alternative, tried anyway, kind `memory` when nothing fits;
    - the cap in the child, `ctx.memory_free_mb()`, `ctx.fallbacks`, and one retry after `oom`
      with a strictly smaller fit;
    - what each machine learns (`<data>/memory/learned.json`), `made_with` on every output, a
      cache that never serves less than this machine can make, and `<data>/settings.json`;
    - `nodes.fit`, `nodes.forget`, and `npm run bench:fit`.

    Checked by tests on the processor and in the tiny runtime. On the owner's GTX 1070 (AC8,
    [2026-10-02-ac8.md](../specs/002-fit-to-memory/reports/2026-10-02-ac8.md)): estimates within
    0.1% of the peak at three settings, the fit under another program's load, and the cap refusing
    an overrun while the card had room, answered by `ctx.fallbacks` and by the engine's retry.
- **Built-in nodes:** `source.image` and `convert.depth_to_points`.
- **Electron 44 shell:**
    - a sandboxed page that reaches the engine through one IPC door with a method allowlist;
    - the engine client;
    - a rotating log;
    - a first page that lists nodes.
- **Checks:**
    - `npm run check`: eslint 10, tsc 7 over JSDoc, and node tests;
    - `npm run engine:check`: ruff, pyright and pytest;
    - `npm run versions`;
    - Dependabot, and the weekly version check.
- **Session setup:** `.claude/hooks/session-start.sh` installs the Node and uv the repo pins, the
  engine environment and the npm packages.
- **Process guardrails:**
    - `AGENTS.md` holds the rules for any agent, and `CLAUDE.md` imports it;
    - the spec workflow is in `specs/`;
    - hooks format after every edit and run the checks before a turn ends;
    - `scripts/test-guard.js` blocks removing a test without a reason;
    - `scripts/check-docs.js` catches docs that name missing files;
    - the PR template checks the acceptance criteria.

Not started: model store, any model node, workspace UI, viewer, installer.

Follow-up: `npm run versions` does not read runtime locks yet, so a runtime's torch pin is checked by
hand.

## Next, in order

Each step becomes a spec in `specs/` ([specs/README.md](../specs/README.md)): spec, owner
approval, plan, owner approval, tasks, implementation, PR. The list below is the order; the specs
hold the detail.

| Spec | Status |
| --- | --- |
| [001 Runtime manager](../specs/001-runtime-manager/spec.md) | done: every acceptance criterion verified, AC9 by the owner's GTX 1070 report ([2026-09-29-gtx1070.md](../specs/001-runtime-manager/reports/2026-09-29-gtx1070.md)); merged in [PR #3](https://github.com/cbgithub7/oneframe-lab/pull/3) |
| [002 Fit to memory](../specs/002-fit-to-memory/spec.md) | spec approved 2026-09-30 after the review and [research](../specs/002-fit-to-memory/research.md), plan approved 2026-10-01; done: AC1–AC7 by tests; AC8's spill clause amended 2026-10-02 (judged by torch's own out-of-memory message, not Windows' shared memory counter) and passed on the owner's GTX 1070 ([2026-10-02-ac8.md](../specs/002-fit-to-memory/reports/2026-10-02-ac8.md)); merged in [PR #5](https://github.com/cbgithub7/oneframe-lab/pull/5) |
| [003 Model store](../specs/003-model-store/spec.md) | spec drafted 2026-10-02 and revised the same day after the [research](../specs/003-model-store/research.md); the owner answered its eight questions 2026-10-02, accepting each recommendation (the downloader to be double-checked); a second review is under way, then approval |

1. **Runtime manager.** A node family's uv environment, built from a committed lock file:
    - Python and torch are chosen per family.
    - The torch build is chosen by compute capability and driver: cu126 below compute 7.5 or on a
      driver older than 580, otherwise cu130. The floor is torch 2.6.
    - Compiled extensions are classed as stand-in, optional or required.
    - The environment is rebuilt only when its lock file changes.

    The scheduler's `runtime_python` hook is where it plugs in.
2. **Fit to memory** (done). How the best local AI apps do it,
   without asking anyone to run a test ([research](../specs/002-fit-to-memory/research.md)):
    - each node declares a memory model: its weights per precision, and its working memory as a
      function of its settings;
    - before loading, the engine measures free memory, keeps a margin, and changes only settings
      the person left alone: speed-only settings first, quality last, and labels the result;
    - one narrow retry on `oom`; measured peaks correct the estimate on each machine.
3. **Model store** (spec drafted). Download for a node's weights and companion files, into
   `<data>/models/<node>`, with resume and sha256, and a Hugging Face snapshot layout. Pin every
   repository revision. The draft, revised after the
   [research](../specs/003-model-store/research.md), recommends one copy per sha256, with a view
   in the Hugging Face layout per runtime, in place of a folder per node (its decision 1, accepted 2026-10-02).
   The research also found the runtime manager's torch floor out of date: 2.10, not 2.6
   (CVE-2026-24747); the owner accepted fixing it on its own (decision 6).
4. **Keep models loaded** (decided 2026-09-30). A long-lived worker per runtime, in place of one
   child process per run: a model loads once and runs many times. The worker stays while idle for
   a set time, is evicted when another runtime needs the device, and is killed on Stop. It
   changes the executors, Stop and the node API (loading separate from running).
5. **First model nodes,** enough for both loops:
    - depth: Depth Pro and MoGe-2;
    - segmentation: SAM 2.1;
    - one object generator: TripoSR or Hunyuan3D-2mini, first because they can be verified on the
      test card (the catalogue is not limited to what fits it);
    - one view synthesiser and one reconstructor: pick from the proposal's catalogue by what runs
      locally;
    - render: an asset to a ViewSet along a CameraPath;
    - evaluate: agreement with the source photo from the input camera.

    Each gets a CPU or tiny path for CI, and says plainly what is unverified.
6. **Workspace UI.**
    - Variants side by side per photo, then a graph editor (Drawflow or a hand-written SVG editor).
    - A three.js plus Spark 2 viewer for meshes, splats and the 2.5D photo, with clay, wireframe
      and trust shading.
7. **Packaging** (phase 2 of the roadmap):
    - an NSIS per-user installer with a bundled, pinned uv;
    - a first-run wizard with preflight checks;
    - a two-tier uninstaller;
    - signing, electron-updater, and a diagnostics bundle.
