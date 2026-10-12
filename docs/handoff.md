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
      At its fullest, a world from one image: the environment as a splat and the movable objects
      as meshes placed by depth, seen together, with its sound: an ambient loop and the objects'
      own sounds (the owner, 2026-10-05). `neilsonnn/image-blaster` makes one with closed, paid
      services; here every step is a local, open node.
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
- **How work is done** (2026-10-02). One spec document, plan included, with one owner approval;
  agents make reversible choices and log them; feasibility questions are spikes. A hardware report
  from any real machine counts, the owner's or a rented one. v1 is Windows or Linux on x86-64,
  NVIDIA or the processor. Storage: one data root, versioned formats, safe deletes, dev and
  packaged roots apart ([AGENTS.md](../AGENTS.md)).

## State (2026-10-12)

Done and green (local, and GitHub Actions on Windows and Ubuntu):

- **The model store's core** (spec 003, done: merged 2026-10-11 in
  [PR #16](https://github.com/cbgithub7/oneframe-lab/pull/16), AC1 to AC7 by tests in CI on Windows
  and Ubuntu; AC8 passed against the real hosts on the owner's Windows PC on 2026-10-12
  ([report](../specs/003-model-store/reports/2026-10-12-ac8-live-hosts.md)); the report's three
  findings fixed after it, not yet run again on that PC):
    - the files a node reads, declared in its manifest (`hf` at a full commit, or an `https` URL,
      with size, sha256, format and `gated`; checkpoints of a `files_by` param), and checked all at
      once;
    - the store under `models/`: each file once by sha256, read-only, with a record of what was
      checked; a newer or unreadable store or record left alone;
    - a transport on `http.client` that heals itself (fresh redirects each attempt, retries,
      stalls, strict ranges, the pinned size before the body, https only, truststore, the system's
      proxy). Stop is seen by the reader in slices of 0.1 s: shutting a socket down from another
      thread does not wake a read on Windows (CI, 2026-10-11). Each attempt journals the hosts it
      asked, names and ports only, never a signed path or query
      ([PR #18](https://github.com/cbgithub7/oneframe-lab/pull/18));
    - `models.download`, `models.verify`, `models.stop`, `models.list` and `models.remove`, one job
      at a time across processes, a job letting go of the store before its ending is sent; none is
      on the page's method list yet;
    - runs read only what is present, through `ctx.file`; a missing file fails with kind `files`
      before any process starts; the fit never changes to a checkpoint that is not downloaded;
    - `npm run models:pin` and `npm run models:download`, which wait up to 10 s for a store another
      process still holds; every tool that opens a data root starts through `scripts/tool.js`, which
      keeps uv's temporary files under that root.

    Against the real hosts (AC8): five files, 11.3 GB, pinned with `models:pin` and fetched with
    `models:download` from Apple's server, Hugging Face (`huggingface.co`, then its CDN) and a
    GitHub release asset (`github.com`, then `release-assets.githubusercontent.com`). Each was
    stopped midway and resumed from the byte it kept, and every sha256 matched; the GitHub asset
    resumed 3,855 s after its Stop, through a fresh redirect; Apple's server dropped a connection on
    its own, and the transport resumed it. One connection ran at 34 to 88 MB/s; the line's own
    capacity was not measured, so whether parallel ranges would help is still open. Spec 005 starts
    from the live pins in `specs/003-model-store/live/`.

- **Foundations** (spec 006, done: merged 2026-10-05 in
  [PR #7](https://github.com/cbgithub7/oneframe-lab/pull/7), its local checks passed on Windows on
  2026-10-11 ([report](../specs/006-foundations/reports/2026-10-11-local.md));
  [reviewed 2026-10-04](reviews/2026-10-04-spec-006.md)):
    - one data root, computed alike by the engine (`oneframe/layout.py`) and the app
      (`app/main/paths.js`) from a shared table; the dev root is `OneframeLab-dev`
      (`oneframe-lab-dev` on Linux), and only the app chooses the packaged one;
    - nothing written outside it but the exceptions AGENTS.md names (the graphics driver's shader
      cache; on Linux, Chromium's single-instance socket in the system temporary folder), and two
      gaps listed below (uv's lock from the npm tools; on Windows, a library cache outside the
      variables `child_env` sets). The
      engine's side is proved by a footprint test on both systems, library caches, temporary files
      and bytecode included; Electron's folders by a unit test of `app/main/boot.js`, a headless
      run on Linux, and the real app on the owner's Windows PC (the local checks);
    - every kept file says its format (`oneframe-root.json`, settings, learned memory, the runtime
      marker), and a newer one is left alone; a runtime moved to another root reads as `moved`;
    - a locked folder per process under `cache/tmp/`, and a lock per runtime install or remove;
    - one failure model (`oneframe/errors.py`, its table in [architecture.md](architecture.md#failures)),
      answered to the page as values;
    - a journal per run and install, pruned, and `npm run diagnose`;
    - cache keys that follow a node's code and its runtime's build (`KEY_VERSION` 2).

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
    [2026-10-02-ac8.md](../specs/002-fit-to-memory/reports/2026-10-02-ac8.md)):
    - measured peaks within 0.1% of the estimates at three settings. The test node allocates
      exactly what its memory model says, so this proves the measuring and the cap work, not that a
      formula predicts a real model; no real model's memory model has been measured yet;
    - the fit under another program's load;
    - the cap refusing an overrun while the card had room, answered by `ctx.fallbacks` and by the
      engine's retry.
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

Not started: any model node, workspace UI, viewer, installer, the model store's view per runtime.

Follow-up:

- `npm run versions` does not read runtime locks yet, so a runtime's torch pin is checked by hand.
- From the sweep after spec 003's AC8 findings, not yet fixed:
    - `runtime.stopped` and `runtime.failed` (`oneframe/runtimes.py`), and a graph run's ending
      (`run.done` before `Engine.graph_run` clears its run), are still sent before their place is
      let go, as model jobs' endings were: a client that acts on hearing one can be refused `busy`;
    - on Windows a Stop kills a node's process, a venv launcher, and at once removes its folder,
      while the interpreter behind it may still be ending (not verified there; the comment in
      `oneframe/executors.py` says a kill does not take the interpreter down, and CPython 3.14's
      launcher says it does);
    - `test_a_killed_install_is_not_installed_and_a_second_install_completes` installs again right
      after killing an engine that held the runtime's lock, and does not check the reply.

## Next, in order

The order and the process were decided by the owner on 2026-10-02, after the
[review of the project as a whole](reviews/2026-10-02.md): build one real vertical slice before
more infrastructure. Each step is one spec document in `specs/` ([specs/README.md](../specs/README.md))
with one owner approval, or a time-boxed spike that ends in a report.

| # | Step | Spec | Status |
| --- | --- | --- | --- |
| 0 | Fixes from the review | none | done 2026-10-03, on this branch, each its own commit with a test |
| 1 | Foundations: one data root, nothing written outside it, versioned formats, locks, one failure model, a journal and `diagnose`, cache keys from code | [006](../specs/006-foundations/spec.md) | done: merged 2026-10-05 ([PR #7](https://github.com/cbgithub7/oneframe-lab/pull/7)); local checks passed on Windows 2026-10-11 ([report](../specs/006-foundations/reports/2026-10-11-local.md)) |
| 2 | Model store, core: pinned files, one copy per sha256, a download that heals itself, runs offline through `ctx.file` | [003](../specs/003-model-store/spec.md) | done: merged 2026-10-11 ([PR #16](https://github.com/cbgithub7/oneframe-lab/pull/16)), AC1–AC7 by tests in CI; AC8 passed against the real hosts on Windows 2026-10-12 ([report](../specs/003-model-store/reports/2026-10-12-ac8-live-hosts.md)), its three findings fixed after it; core approved 2026-10-03 ([research](../specs/003-model-store/research.md)), amended 2026-10-05 |
| 3 | First light: Depth Pro and MoGe-3 (vitl, chosen by the owner 2026-10-03) run from the app, two variants side by side in a minimal viewer, with a photo picked in a main-process dialog and a result protocol the page can read (a large mesh or splat included); MoGe-3 may not run on the 1070 (see Known) | 005 (first part) | next: no spec yet; it starts from the pins AC8 wrote in `specs/003-model-store/live/` |
| 3b | The model store's view per runtime, in the Hugging Face layout (`refs/main`, hard links, `ctx.snapshot`), which TripoSR is the first to need | 003 (view part) | not started |
| 4 | SAM 2.1, and an object generator that needs a compiled extension (TripoSR or Hunyuan3D-2mini) | 005 (second part) | not started |
| 5 | Keep models loaded: a long-lived worker per runtime, sized by the load times measured in steps 3 and 4 | 004 | not started |
| 6 | Improve-loop spike: one view synthesiser and one reconstructor on some machine, and whether WSL is needed | spike | not started |
| 7 | Workspace UI, grown from step 3's page: variants, a graph editor, the three.js and Spark viewer | new | not started |
| 8 | The rest of 003 (import, a movable store, a queue, the page's part) and packaging (installer, two-tier uninstaller, signing, updates) | 003, new | not started |

Done before this order:

| Spec | Status |
| --- | --- |
| [001 Runtime manager](../specs/001-runtime-manager/spec.md) | done: every acceptance criterion verified, AC9 by the owner's GTX 1070 report ([2026-09-29-gtx1070.md](../specs/001-runtime-manager/reports/2026-09-29-gtx1070.md)); merged in [PR #3](https://github.com/cbgithub7/oneframe-lab/pull/3) |
| [002 Fit to memory](../specs/002-fit-to-memory/spec.md) | done: AC1–AC7 by tests; AC8 on the owner's GTX 1070 ([2026-10-02-ac8.md](../specs/002-fit-to-memory/reports/2026-10-02-ac8.md)); merged in [PR #5](https://github.com/cbgithub7/oneframe-lab/pull/5) |

Known from the review, to be settled by the specs above or later ones:

- **Not yet taken on from the [review of 2026-10-02](reviews/2026-10-02.md)** (spec 006 answered
  section 8B, section 5's escapes from the root and shared roots, and items 4.4 and 4.6 to 4.8):
    - 4.2 (High): requests are answered on the engine's input loop, so a slow `runtimes.remove`, or
      `nodes.fit` waiting on nvidia-smi, holds up `run.stop`;
    - the rest of 4.5: two lists of variables kept from children (`executors.py`,
      `runtime_install.py`), two ways to kill a process (the scheduler is now built in one place,
      `scheduler.for_root`, spec 003);
    - 4.10: `memory.py` is four modules, and `bench_fit.py` names the runtime `torch`;
    - 4.11: pyright on the first node that imports torch, and a cached CI job for real torch
      (spec 005);
    - before spec 004: an injected executor provider, and node code loaded as a package named for
      the node; today two engine nodes with same-named helpers can run each other's code;
    - before spec 004 too: split the child into a once-per-process bootstrap and per-job code,
      joined by a typed job (006's Out of scope);
    - 4.9: a machine-readable protocol contract, and consistent event names (006's Out of scope);
    - 4.10 also: `cache.py` imports the child's `Value`;
    - the low tier: `allow_network` is read and never set; the test guard notices only deleted
      test names; the docs check proves only that paths exist;
    - section 5, with packaging: a chosen root is not checked for long paths, OneDrive or FAT32;
    - section 3: the build-versus-adopt decision against ComfyUI, still unwritten.
- **From the [review of spec 006](reviews/2026-10-04-spec-006.md)**, small and open:
    - a `memory` failure says `retry` even when this machine can never run the node;
    - a step log has no bound while its run lasts;
    - disk and lock errors are reported as engine bugs;
    - on Windows, a library cache outside the variables `child_env` sets still goes to the person's
      profile;
    - a runtime node running in one process is not protected from a remove or reinstall in another,
      and an install in another process reads as `not_installed` here;
    - spec 004's long-lived worker must keep what 006 assumes of one process per job: node code
      imported fresh, `child_env` per job, a scratch folder per process.

- **MoGe-3 may not run on the owner's GTX 1070** (read in MoGe's source, 2026-10-05). Its code
  imports FlexGEMM, which is built on Triton, and Triton's README supports NVIDIA cards from compute
  8.0; FlexGEMM runs only in MoGe-3's optional refinement (`refine_steps`), and the `moge` package
  requires it for every version. Whether MoGe-3, or MoGe-2 through that package, installs and runs
  on an older card or on Windows is unverified, and spec 005's to check. If it does not run on the
  1070, step 3 uses MoGe-2 there instead (the owner, 2026-10-11).
- **For a world from one image** (Generate at its fullest), beyond what steps 3 to 7 plan (the
  result protocol, compiled extensions, the viewer):
    - lighter and heavier models are separate nodes to choose between; a world model tested only on
      datacenter cards under Linux (NVIDIA's Lyra 2, say) gets a runtime the plan refuses where it
      cannot run;
    - `.spz` and `.obj`: export nodes beside `.glb` and `.ply`, or new port types (not decided);
    - the token part spec 003 leaves for later, before any gated model;
    - sound, in scope since 2026-10-05: an audio port type, nodes that make it, and a player in
      the page; nothing carries audio yet, and no spec has taken it on.
- **Claude desktop on Windows is an MSIX package** (spec 006's local report). A process it starts
  writes a new folder under `AppData`, and any `HKCU` key, into the package's private storage, while
  `dir` and `reg query` from inside show the merged view. A local check of where things land runs
  from a terminal started outside it; `npm run diagnose` names the root's real path. A packaged
  Oneframe Lab built as MSIX would have its writes redirected the same way.
- **Windows, from spec 003's AC8 report** ([findings](../specs/003-model-store/reports/2026-10-12-ac8-live-hosts.md),
  fixed after it). Windows lets go of a killed process's locks a moment after it has ended, so a
  test that ends a lock's holder waits for the lock (`wait_free` in `engine/tests/conftest.py`).
  Under `npm run`, Ctrl+C gives the prompt back before the tool has stopped; only the model tools
  allow for it, by waiting for a store still held.
- **The footprint test can fail on another program's write** (not yet taken on: spec 003 closed
  without it; any session may). On PR #11's Windows CI (2026-10-11), `test_ac2_nothing_is_written_outside_the_root` found a
  new `Mozilla` folder in the runner's real `AppData\Roaming`, almost surely the runner's own
  Firefox; the next run passed. The check should tell our processes' writes from others' without
  checking less, and a change to it is listed in spec 006's Tests section (narrowing it is the
  owner's call).
- **Precision has no speed model.** The fit can pick fp16 on a card that runs it far slower than
  fp32. The rate per precision and compute capability is published data, so it can be added
  without naming a card. The margin (1.5 GiB or 10%) should be derived again from real runs.
- **The cache has no size cap**, and no spec defines a recipe format yet.
- **A public release is a goal** (the owner, 2026-10-03; review decision 8). That makes real
  work of:
    - a licence: the app's own code is MIT (the owner, 2026-10-03; `LICENSE`), and notices for
      third-party code are still to come;
    - signing;
    - a cleared product name;
    - documentation for users, apart from these docs for agents;
    - an accessibility baseline.

  The packaging and UI steps carry them.
- **Waiting on the owner:** `main` has no branch protection today (GitHub reports
  `protected: false`), so "only through a PR with green CI" is a convention, not enforced.
  Protecting it, with the two CI checks required, comes before Dependabot's minor updates may merge
  themselves.
