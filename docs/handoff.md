# Handoff: where the project is and what comes next

Read this first in a new session, then [architecture.md](architecture.md) and [nodes.md](nodes.md).
The rules are in [AGENTS.md](../AGENTS.md); what is parked is in [roadmap.md](roadmap.md). The wider
proposal (why this design, the survey of about 50 models) is the doc "Oneframe Lab: foundation
proposal": https://claude.ai/code/artifact/9df2417a-7122-415a-8b5d-f65daf5bc123

## What it is

- **A playground for making 3D from one photo:** an editor where a person builds graphs of nodes
  and compares what they make (the owner, 2026-10-12). Personal, free, non-commercial and local
  only: no hosted models, no accounts, no network during a run; weights download when a person
  presses Download. Non-commercial model licences are fine; licence data is shown, not used to
  block.
- **Two loops.**
    - *Generate:* a photo → a depth scene, a mesh, a splat, or segmented objects placed in a scene.
      At its fullest, a world from one image: the environment as a splat and the movable objects
      as meshes placed by depth, seen together, with its sound: an ambient loop and the objects'
      own sounds (the owner, 2026-10-05). `neilsonnn/image-blaster` makes one with closed, paid
      services; here every step is a local, open node.
    - *Improve:* a photo, or a first result rendered along a camera path → synthesised views →
      reconstruction → a better result, compared against the first.
- **Who runs it:** Windows or Linux on x86-64, with an NVIDIA card or the processor. The owner's
  GTX 1070 (8 GB, compute 6.1) is the only card we test on, never the target. The cloud container
  has no GPU and cannot reach Hugging Face.
- **A public release is a goal;** the app's own code is MIT. `cbgithub7/depth-pro-gui`, the owner's
  earlier app, may be read for what it learned; nothing is ported or copied from it, and nothing
  here has to match it.

## State (2026-10-30)

Green in CI on Windows and Ubuntu, each spec with its reports:

| Spec | What it gave | Done |
| --- | --- | --- |
| [001](../specs/001-runtime-manager/spec.md) | Runtimes: definitions, a build planned from what the machine reports, install, remove, `bench:runtime` | AC9 on the GTX 1070; [PR #3](https://github.com/cbgithub7/oneframe-lab/pull/3) |
| [002](../specs/002-fit-to-memory/spec.md) | Fit to memory: a node's memory model, the fit before each load, the cap, what each machine learns, `bench:fit` | AC8 on the GTX 1070; [PR #5](https://github.com/cbgithub7/oneframe-lab/pull/5) |
| [006](../specs/006-foundations/spec.md) | One data root, versioned files, locks, one failure model, journals, `diagnose`, cache keys from code | local checks on Windows; [PR #7](https://github.com/cbgithub7/oneframe-lab/pull/7) |
| [003](../specs/003-model-store/spec.md) (core) | The model store: pinned files kept once by sha256, a download that heals itself, runs offline through `ctx.file`, `models:pin` and `models:download` | AC8 against the real hosts on Windows; [PR #16](https://github.com/cbgithub7/oneframe-lab/pull/16), [PR #22](https://github.com/cbgithub7/oneframe-lab/pull/22) |
| [007](../specs/007-test-guard/spec.md) | The test guard sees every collected case, a skip or an emptied body, and renames; `engine/tests/collected.txt` is committed | CI on Windows and Ubuntu; [PR #28](https://github.com/cbgithub7/oneframe-lab/pull/28) |
| [008](../specs/008-processes/spec.md) | One module starts, reads and ends processes, and Stop ends a node's whole tree; one module names what a child inherits; one stop type; a hang names itself | CI on Windows and Ubuntu; [PR #35](https://github.com/cbgithub7/oneframe-lab/pull/35) |
| [009](../specs/009-node-api/spec.md) | The node API apart from the child script, which is a bootstrap; a typed job; node code loaded as a package named for the node; an optional `load` before `run`; `ctx` typed by `Context` and `LoadContext` | CI on Windows and Ubuntu; [PR #37](https://github.com/cbgithub7/oneframe-lab/pull/37) |
| [010](../specs/010-scheduler/spec.md) | The scheduler is given its executors (one process per run by default, so spec 004's worker pool can take its place); one `Emit`; one probe helper for the benches; `_attempt` in named steps; every ending sent after its place is free | CI on Windows and Ubuntu; [PR #39](https://github.com/cbgithub7/oneframe-lab/pull/39); the probes on the GTX 1070 ([report](../specs/010-scheduler/reports/2026-10-25-local.md)) |
| [011](../specs/011-files/spec.md) | One file hash, one move into place and one free space, in `files.py`; runtime sources fetched by the model store's transport (https only, retried, resumed), which now reads GitHub's archives; every path from the layout, `cache/objects` included | CI on Windows and Ubuntu; [PR #44](https://github.com/cbgithub7/oneframe-lab/pull/44) |
| [012](../specs/012-modules/spec.md) | Memory and runtimes as packages whose roots hold only a docstring, so reading a manifest loads only the memory model, and planning neither the manager nor the install; one way to read a definition (`reading.py`), where a malformed one is a problem, never a crash; `parse` and the install in named steps | CI on Windows and Ubuntu; [PR #47](https://github.com/cbgithub7/oneframe-lab/pull/47) |
| [013](../specs/013-hard-coded/spec.md) | Nothing in the engine names a runtime, a node or the tests, which a test holds; spec 002's check on a card lives with its node as `npm run bench:fit-check`, and `bench:fit` needs a node; names outside v1 (devices `mps`, `xpu`, `rocm`; vendors `amd`, `intel`; OS `macos`) refused | CI on Windows and Ubuntu; [PR #49](https://github.com/cbgithub7/oneframe-lab/pull/49); the check on a card on the GTX 1070 ([report](../specs/013-hard-coded/reports/2026-10-30-local.md)) |
| [014](../specs/014-tests/spec.md) | Each test helper once, in `conftest.py` and `engine/tests/fixtures/`; every engine a test starts, in-process or as a process, ended by a fixture, its whole tree; every wait with a deadline that names what it waited for; wall-clock bounds only where they are evidence, marked; the data root's exceptions as `outside` in `contracts/layout.json`, which the footprint test reads, and the real profile checked deep in our programs' default folders | CI on Windows and Ubuntu; [PR #51](https://github.com/cbgithub7/oneframe-lab/pull/51) |

Beside them: the engine's core (typed ports, manifests, graph planning with converters, the cache,
the scheduler, the NDJSON server); an Electron 45 shell whose sandboxed page lists nodes through
one door; two built-in nodes (`source.image`, `convert.depth_to_points`); one runtime, `torch`,
with no model code. No model node, editor or viewer yet.

## Next, in order

The [review of 2026-10-12](reviews/2026-10-12.md) set this order with the owner: clean code
before the first model node, and the Improve-loop spike now, beside first light. Steps keep the
numbers the reviews use; steps 1 and 2 (specs 006 and 003) are done, and so is 2a, the review's
small fixes and process rules ([PR #24](https://github.com/cbgithub7/oneframe-lab/pull/24)), and 2b, the clean-up
(specs 007 to 014). Step 3 is next.

| # | Step | Spec | Status |
| --- | --- | --- | --- |
| 2b | Clean-up: the review's sections 3 and 4 ([review](reviews/2026-10-12.md)), in eight specs worked in this order: [the test guard](../specs/007-test-guard/spec.md), [processes and Stop](../specs/008-processes/spec.md), [the node API](../specs/009-node-api/spec.md), [the scheduler and its executors](../specs/010-scheduler/spec.md), [files, hashes, downloads and paths](../specs/011-files/spec.md), [modules](../specs/012-modules/spec.md), [nothing hard-coded](../specs/013-hard-coded/spec.md), [tests](../specs/014-tests/spec.md). Nothing a person sees changes except where their open questions say; the node contract changes once (009), before any model node depends on it | 007 to 014 | approved 2026-10-18, every open question as recommended; six changed after `/code-review` (a line under each status) and approved again 2026-10-23; 007 done ([PR #28](https://github.com/cbgithub7/oneframe-lab/pull/28)); 008 done ([PR #35](https://github.com/cbgithub7/oneframe-lab/pull/35)); 009 done ([PR #37](https://github.com/cbgithub7/oneframe-lab/pull/37)); 010 done ([PR #39](https://github.com/cbgithub7/oneframe-lab/pull/39)); 011 done ([PR #44](https://github.com/cbgithub7/oneframe-lab/pull/44)); 012 done ([PR #47](https://github.com/cbgithub7/oneframe-lab/pull/47)); 013 done ([PR #49](https://github.com/cbgithub7/oneframe-lab/pull/49)); 014 done ([PR #51](https://github.com/cbgithub7/oneframe-lab/pull/51)); the clean-up is done |
| 3 | First light, in the editor's shape: Depth Pro and MoGe-3 (vitl) from the page, which holds a graph the person can change, two variants side by side; nodes listed by the engine, settings from manifests, graphs that ship as data and keep the page's own fields, viewers by port type, results served by key, a photo picked in a main-process dialog. Proposed for the owner with the spec: "what fits here" answered by the engine | 005 | next: its spec is to draft; it starts from the pins in `specs/003-model-store/live/` |
| 6 | Improve-loop spike, beside step 3: one view synthesiser and one reconstructor, and whether WSL is needed, on a rented GPU (the 1070 cannot run the view synthesisers) | spike | agreed 2026-10-12; waiting on the owner to rent one |
| 3b | The model store's view per runtime, in the Hugging Face layout, which TripoSR is the first to need | new (003's view part) | not started |
| 4 | SAM 2.1, and an object generator that needs a compiled extension (TripoSR or Hunyuan3D-2mini) | 005 (second part) | not started |
| 5 | Keep models loaded: a long-lived worker per runtime, sized by the load times steps 3 and 4 measure | 004 | not started |
| 7 | The editor, grown from step 3's page: a canvas, variants side by side, the three.js and Spark viewer; wiring aids as the owner decides (roadmap) | new | not started |
| 8 | The rest of 003 (import, a movable store, a queue, the page's part), and packaging (installer, two-tier uninstaller, signing, updates) | new | not started |

## Waiting on a local session

What only real hardware can run, for `/local-session` (`.claude/skills/local-session/SKILL.md`)
to take up on the owner's PC, in this order; the owner picks which. Each item gives its branch,
its commands (run from the repo's root), what passing looks like and where its report goes, and
leaves this list in the PR that commits its report.

Nothing is waiting. The last item passed on 2026-10-30 on the GTX 1070: spec 013's check on a
card ([report](../specs/013-hard-coded/reports/2026-10-30-local.md)), run by Grok Bot outside
Claude desktop's package. Before it, on 2026-10-25: spec 010's probes on a card
([report](../specs/010-scheduler/reports/2026-10-25-local.md)) and the torch runtime's new lock
([report](../specs/001-runtime-manager/reports/2026-10-25-torch-lock-local.md)).

## Open, and in the way of the next steps

- **For first light (step 3):**
    - requests are answered on the engine's input loop, so a slow `runtimes.remove`, or
      `nodes.fit` waiting on nvidia-smi, holds up `run.stop` (review of 2026-10-02, 4.2);
    - pyright on the first node that imports torch, and a CI job with real torch on the processor
      (review of 2026-10-02, 4.11); CI does not start the app either;
    - `models.*` and `nodes.fit` are not on the page's method list.
- **MoGe-3 may not run on older cards, or on Windows.** Its code imports FlexGEMM, built on
  Triton, which supports NVIDIA cards from compute 8.0 and publishes Linux wheels only; FlexGEMM
  runs only in MoGe-3's optional refinement, and the `moge` package requires it for every version.
  Whether MoGe-3, or MoGe-2 through that package, installs and runs on an older card or on Windows
  is unverified, and spec 005's to check. If MoGe-3 does not run on the 1070, step 3 uses MoGe-2
  there (the owner, 2026-10-11).
- **A runtime install started from inside Claude desktop fails on the owner's PC** (2026-10-25):
  four times uv stopped with os error 32 on a folder it had just emptied, with uv 0.13.5 and
  0.12.20 and either lock, while the same installs from outside the package passed
  ([report](../specs/001-runtime-manager/reports/2026-10-25-torch-lock-local.md)). The
  local-session skill sends only footprint checks outside the package; whether installs go there
  too, and whether the engine's install should outlast such a refusal, is open.
- **Waiting on the owner:** `main` has no branch protection, so "only through a PR with green CI"
  is a convention. Protecting it, with the CI checks required, comes before Dependabot's minor
  updates may merge themselves.
