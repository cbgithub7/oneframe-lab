# Roadmap: what is parked

What has been decided or found, but that no step has taken on yet. The order of work is in
[handoff.md](handoff.md); a step that takes an item on moves it into its spec and deletes it here.

## For the clean-up spec (007)

From the [review of 2026-10-12](reviews/2026-10-12.md), section 3, which the owner asked to clean
up before the first model node. Nothing a person or a node sees changes.

- **The node contract:**
    - an optional `load(ctx)` before `run(ctx, state)`, so spec 004 keeps models loaded without
      rewriting nodes;
    - node code loaded as a package named for the node: a helper called `utils` hides a package of
      that name today, and two engine nodes with same-named helpers can run each other's code;
    - a node API module apart from the child script, which `errors.py`, `cache.py` and
      `scheduler.py` import today (review of 2026-10-02, 4.10).
- **Modules that are several:**
    - `oneframe/memory.py` is five: the model and its parser, settings, devices and the estimate,
      the fit, and the learned store;
    - `oneframe/runtimes.py` is three: definitions, the plan, the manager;
    - `oneframe/child.py` holds five concerns;
    - `oneframe/manifest.py` imports all of memory, the learned store and settings included, to
      parse a manifest.
- **One way each** (review of 2026-10-02, 4.5):
    - the variables kept from children, two lists today that differ in members and in case;
    - starting a process, reading its lines, and killing it (three ways);
    - `NO_WINDOW`, the `Emit` alias, stop signalling, a rename retry on Windows;
    - a file's sha256, computed in five places;
    - downloads: runtime sources still use urllib, without resume (`oneframe/archives.py`);
    - a job's dict, built in three places and read untyped;
    - paths only from the layout.
- **Ready for spec 004:** a swappable executor and a typed job, and the child split into a
  once-per-process bootstrap and per-job code joined by that job (spec 006's Out of scope).
- **"Nothing hard-coded":** `oneframe/bench_fit.py` names the runtime `torch`, and ships spec
  002's AC8 scenario inside the package (review of 2026-10-02, 4.10).
- **Long functions:** the runtime install (179 lines), the scheduler's `_attempt` (123), the
  manifest's `parse` (118); `Scheduler.__init__` takes 15 parameters.
- **The download transport passed its own tripwire:** spec 003's research said to switch to
  urllib3 past about 600 lines, and it is 754. Keep it (it passed the real hosts), record why, and
  add nothing more to it.
- **Tests:**
    - shared helpers for the model tests;
    - the test guard: it sees neither the 139 parametrized cases nor skip marks, and a test cannot
      be renamed;
    - the footprint test tells our writes from another program's without checking less (it failed
      once on a runner's own Firefox), and reads the data root's exceptions as data. Narrowing it
      is the owner's call;
    - `test_a_killed_install_is_not_installed_and_a_second_install_completes` does not check its
      second install's reply;
    - nine assertions bound wall-clock time;
    - two tests check implementation details: the cache key's version, and the tool's private
      `_claimed` and `_wait`.
- **Dead weight:** `allow_network` is read and never set; the device names `mps`, `xpu` and
  `rocm`, and the `amd`, `intel` and `macos` options, are outside v1's platforms.

## The editor (step 7)

- **Wiring aids.** Layer 1, "what fits here" answered by the engine, belongs in spec 005. Then:
    - wiring a new node's input that exactly one output fits, and asking when several do;
    - a goal search ("a photo to a splat"), bounded in depth, ranked by what this machine can run,
      what is downloaded, and trust.

  A facet that a setting decides can be offered only as "fits if the setting is x", which needs
  the manifest to say which value gives which facet.
- **A verification label per node** in the catalogue ("verified on …", "CI only", "unverified"),
  accepted on 2026-10-02.

## A world from one image

Generate at its fullest, beyond what steps 3 to 7 plan:

- lighter and heavier models are separate nodes to choose between; a world model tested only on
  datacenter cards under Linux (NVIDIA's Lyra 2, say) gets a runtime the plan refuses where it
  cannot run;
- `.spz` and `.obj`: export nodes beside `.glb` and `.ply`, or new port types (not decided);
- sound, in scope since 2026-10-05: an audio port type, nodes that make it, and a player in the
  page.

## Memory and speed

- **Precision has no speed model.** The fit can pick fp16 on a card that runs it far slower than
  fp32. The rate per precision and compute capability is published data, so it can be added
  without naming a card.
- **The margin** (1.5 GiB or 10%) should be derived again from runs of real models.
- **The cache has no size cap.**

## The engine

- A machine-readable protocol contract, and consistent event names: `progress`,
  `runtime.progress` and `model.progress` each have their own fields (review of 2026-10-02, 4.9).
- From the [review of spec 006](reviews/2026-10-04-spec-006.md):
    - a `memory` failure says `retry` even when this machine can never run the node;
    - a step log has no bound while its run lasts;
    - disk and lock errors are reported as engine bugs;
    - on Windows, a library cache outside the variables `child_env` sets still goes to the
      person's profile;
    - a runtime node running in one process is not protected from a remove or reinstall in
      another, and an install in another process reads as `not_installed` here;
    - spec 004's long-lived worker must keep what 006 assumes of one process per job: node code
      imported fresh, `child_env` per job, a scratch folder per process.
- `npm run versions` does not read runtime locks, so a runtime's torch pin is checked by hand.
- The docs check proves only that paths exist.

## Packaging and a public release

A public release is a goal (the owner, 2026-10-03). The app's own code is MIT (`LICENSE`).

- notices for third-party code, signing, a cleared product name, documentation for users, and an
  accessibility baseline;
- a chosen root is not checked for long paths, OneDrive or FAT32 (review of 2026-10-02, section 5);
- licence pages open in the browser: the page's list of external hosts holds only `github.com`;
  with the token part spec 003 leaves for later, before any gated model;
- Claude desktop on Windows is an MSIX package (spec 006's local report): a process it starts
  writes a new folder under `AppData`, and any `HKCU` key, into the package's private storage, while
  `dir` and `reg query` from inside show the merged view. A local check of where things land runs
  from a terminal started outside it. A packaged Oneframe Lab built as MSIX would have its writes
  redirected the same way.

## Known about Windows

From spec 003's AC8 ([findings](../specs/003-model-store/reports/2026-10-12-ac8-live-hosts.md),
[fixes checked](../specs/003-model-store/reports/2026-10-12-ac8-findings-local.md)):

- Windows lets go of a killed process's locks a moment after it has ended, so a test that ends a
  lock's holder waits for the lock (`wait_free` in `engine/tests/conftest.py`).
- Under `npm run`, Ctrl+C gives the prompt back before the tool has stopped; only the model tools
  allow for it, by waiting for a store still held.
