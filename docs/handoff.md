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
- **The old app is a pathfinder, not a baseline.** `cbgithub7/depth-pro-gui` is where proven
  code comes from; nothing has to match its outputs. Attach it read-only when porting.
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
    - engine and runtime-child executors; the child protocol is ported from depth-pro-gui;
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
hold the detail. Where code is ported, the paths are on depth-pro-gui's `main`.

| Spec | Status |
| --- | --- |
| [001 Runtime manager](../specs/001-runtime-manager/spec.md) | approved 2026-09-29; plan and tasks drafted 2026-09-29; next: the owner approves the plan |

1. **Runtime manager.** A node family's uv environment, built from a committed lock file:
    - Python and torch are chosen per family.
    - The torch build is chosen by compute capability and driver: cu126 below compute 7.5 or on a
      driver older than 580, otherwise cu130. The floor is torch 2.6.
    - Compiled extensions are classed as stand-in, optional or required.
    - The environment is rebuilt only when its lock file changes.

    Port from `python/objects/runtime.py`, `manage.py` and `wsl.py`, and the backend data in
    `shared/contracts/generators.json`. The scheduler's `runtime_python` hook is where it plugs
    in.
2. **Attempt ladder.**
    - Manifests gain `arrangements`: device, precision and settings, each with a published VRAM
      figure and its source, best quality first.
    - The scheduler walks them on `oom` under an allocator ceiling.
    - Observations per machine re-order them.

    Port `plan()`, `ceiling_mb` and `run_plan` from `python/objects/__init__.py` and `runner.py`.
3. **Model store.** Download for a node's weights and companion files, into
   `<data>/models/<node>`, with resume and sha256, and a Hugging Face snapshot layout. Port from
   `electron/modelstore.js`. Pin every repository revision.
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

## Porting notes from depth-pro-gui

| Old path | What to take | What to leave |
| --- | --- | --- |
| `python/objects/runtime.py`, `manage.py`, `wsl.py` | uv venv building, torch choice, prebuilt wheels with sha256, resumable install steps, WSL placement | "backend" naming; `.pth` source injection unless a family needs it |
| `python/objects/__init__.py` (`plan`), `runner.py` (`run_plan`) | The attempt ladder, the ceiling, and skipping rungs that failed before | The generator-only job shape (`image`, `output`, `weights`) |
| `python/objects/adapters/*.py` | Model loading details per family (TripoSR, SF3D, SPAR3D, Hunyuan3D, TripoSG, TRELLIS) | The Context API; rewrite them against `NodeContext` |
| `shared/contracts/generators.json` | Pinned commits, requirements, companions, published VRAM figures and sources | The schema; move the data into node manifests and runtime lock files |
| `electron/modelstore.js`, `electron/hwstore.js` | Resumable downloads, snapshot layout, fingerprinted observations | The painter and product specifics |
| `python/detach/{find,place,glb,sam}.py` | Object finding, placement maths, GLB reading | The Fast-render callback shape; these become nodes |
| `python/layered3d.py`, `python/bilateral.py` | The layered-mesh and bilateral-filter maths, for a 2.5D scene node | The dependency on 3d-photo-inpainting |
| `depth-pro-gui/docs/object-generators.md` | The requirements research per model | |
