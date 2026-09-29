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
  The owner's test machine is a GTX 1070 (8 GB, compute 6.1). It is one test machine, not the
  design target: the app should try what might run on any hardware and fail with a clear reason
  when it cannot.

## State (2026-09-29)

Done and green (local, and GitHub Actions on Windows and Ubuntu):

- **Engine** (`engine/src/oneframe/`):
    - port types with facets, manifests and the registry;
    - graph planning with automatic converters;
    - the content-addressed cache and the scheduler, with trust carried through;
    - engine and runtime-child executors, and the child protocol;
    - the NDJSON stdio server.
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

Not started: runtime manager, model store, any model node, workspace UI, viewer, installer.

## Next, in order

Each step becomes a spec in `specs/` ([specs/README.md](../specs/README.md)): spec, owner
approval, plan, owner approval, tasks, implementation, PR. The list below is the order; the specs
hold the detail.

| Spec | Status |
| --- | --- |
| [001 Runtime manager](../specs/001-runtime-manager/spec.md) | spec and plan approved 2026-09-29; in progress on `claude/sweet-davinci-ko7mlh`, following its `tasks.md` |

1. **Runtime manager.** A node family's uv environment, built from a committed lock file:
    - Python and torch are chosen per family.
    - The torch build is chosen by compute capability and driver: cu126 below compute 7.5 or on a
      driver older than 580, otherwise cu130. The floor is torch 2.6.
    - Compiled extensions are classed as stand-in, optional or required.
    - The environment is rebuilt only when its lock file changes.

    The scheduler's `runtime_python` hook is where it plugs in.
2. **Attempt ladder.**
    - Manifests gain `arrangements`: device, precision and settings, each with a published VRAM
      figure and its source, best quality first.
    - The scheduler walks them on `oom` under an allocator ceiling.
    - Observations per machine re-order them.
3. **Model store.** Download for a node's weights and companion files, into
   `<data>/models/<node>`, with resume and sha256, and a Hugging Face snapshot layout. Pin every
   repository revision.
4. **First model nodes,** enough for both loops:
    - depth: Depth Pro and MoGe-2;
    - segmentation: SAM 2.1;
    - one object generator: TripoSR or Hunyuan3D-2mini, the two that have really run on the 1070;
    - one view synthesiser and one reconstructor: pick from the proposal's catalogue by what runs
      locally;
    - render: an asset to a ViewSet along a CameraPath;
    - evaluate: agreement with the source photo from the input camera.

    Each gets a CPU or tiny path for CI, and says plainly what is unverified.
5. **Workspace UI.**
    - Variants side by side per photo, then a graph editor (Drawflow or a hand-written SVG editor).
    - A three.js plus Spark 2 viewer for meshes, splats and the 2.5D photo, with clay, wireframe
      and trust shading.
6. **Packaging** (phase 2 of the roadmap):
    - an NSIS per-user installer with a bundled, pinned uv;
    - a first-run wizard with preflight checks;
    - a two-tier uninstaller;
    - signing, electron-updater, and a diagnostics bundle.
