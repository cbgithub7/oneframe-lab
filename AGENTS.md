# Oneframe Lab: rules for coding agents

A local, single-photo 3D playground: an Electron shell, a Python 3.15 engine run by uv, and nodes
(model adapters) declared by manifests. These are the rules for any agent, kept short and true: a
rule, a command or the layout that changes is changed here in the same change. Each rule names what
holds it; "review" means nothing automatic does yet.

**Start here:** [docs/handoff.md](docs/handoff.md) (state, decisions, what is next), then the spec
you are working on in `specs/`. Read [docs/architecture.md](docs/architecture.md) before changing
anything structural, and [docs/nodes.md](docs/nodes.md) before adding a node. The commands are in
the [README](README.md#commands).

## How work is done

1. **Spec first, in one document.** Anything larger than a small fix has a folder in `specs/` with
   `spec.md`, which holds the plan too, and `tasks.md`, within the limits of
   [specs/README.md](specs/README.md) (the docs check holds the length). The limits are on the
   document, never on the design: work that needs more becomes two specs. An agent reviews the
   draft; the owner approves it once, before code, and again only if its requirements, criteria or
   open questions change. The spec, not the chat, is the source of truth. **A model node needs no
   spec** when nothing outside its folder changes: one PR with its manifest, its code, a test that
   runs it on the processor, and a `bench:fit` report from a real card. A new runtime family, an
   engine, app or contract change, or a licence question still needs one.
2. **Agents decide what can be undone,** and log each choice under "Decisions taken" in one line
   with its reason. The owner decides product behaviour, licences and money, the platform matrix,
   data formats that are hard to change, and these rules.
3. **Spikes answer feasibility** ("does this model run on Windows"): time-boxed, no spec,
   throwaway code, ending in a report the owner reads.
4. **Small steps.** Work through `tasks.md` in order, ticking each item as it lands.
5. **Verify before claiming.** `npm run check` and `npm run engine:check` pass before a turn ends
   and before a PR; the PR checks the spec's acceptance criteria one by one.
6. **Tests are not negotiable.** Never delete or weaken a test to make a check pass. A test or case
   is removed, skipped or emptied only under the spec's "- Removed", with its reason, and renamed
   only under "- Renamed". Held by `node scripts/test-guard.js`, in the hooks and in CI.
7. **The PR is where the owner decides.** An agent's job ends when it opens the PR, which also sets
   the spec's `Status:` to done. Nothing merges to `main` but through a PR with green CI.
8. **Hardware claims need a report:** numbers from a real run on a named real machine (seconds,
   peak VRAM, the fit that finished). Cloud sessions have no GPU; say plainly what is unverified.

## Rules

- **The graph is the person's.** The product is an editor where a person builds graphs; a shipped
  graph is a starting point saved as data. Nothing in the engine or the page depends on one, and
  tests build their own nodes and graphs. Held by review.
- **Nothing hard-coded.** No code outside a node's folder names a node, a model, a runtime or a
  pipeline; the engine learns them from `nodes/*/node.json`, and ports from
  `contracts/port-types.json`. Held by `test_no_engine_module_names_a_runtime_a_node_or_the_tests`.
- **One way to do each thing.** Find the helper that exists (processes, a child's environment, file
  writes, hashing, downloads, paths) before writing one; a second needs its reason in the spec.
  Held by review.
- **Any machine that can run it,** from no GPU to the largest cards. Hardware decisions come from
  what the machine reports (compute capability, memory, driver), never a card's name, and a test
  of one covers a range of machines. The owner's GTX 1070 proves mechanisms and tunes nothing. v1
  is Windows or Linux on x86-64, with an NVIDIA card or the processor. Held by
  `test_names_outside_v1_are_refused` and the fit's tests.
- **Ports are typed, facets included.** Values whose facets differ connect only through a
  converter; disparity never flows into a metric port. Held by
  `test_plan_refuses_type_mismatch_and_cycles` and
  `test_a_facet_mismatch_at_run_time_is_the_edges_failure_not_the_nodes`.
- **Light engine.** Importing `oneframe.server` loads no numpy, Pillow, torch or model library;
  model code runs in its runtime. Held by
  `test_importing_the_server_loads_no_numeric_or_model_library`.
- **No network during a run.** Runtime children refuse connections, are told hubs are offline and
  get no hub token; everything a node reads is fetched by Download first. This stops accidents,
  not malicious code. Held by `test_the_network_is_closed_during_a_run` and
  `test_a_run_is_told_hubs_are_offline_and_gets_no_hub_token`.
- **One door into the page.** The page is sandboxed and isolated, and reaches the engine only
  through `engine:request` with a method from `ENGINE_METHODS` (`app/main/methods.js`); progress is
  pushed as events, and nothing polls. Held by "the door refuses what the page may not ask, as
  values".
- **The page is safe to load.** Its content security policy allows only the app's own files: no
  remote code, style, font or frame, no inline script. It never navigates away, and it reads
  nothing by path. Held by review until the page's tests hold it.
- **The page works for everyone.** Every control is reachable and usable from the keyboard, with
  focus shown and a name a screen reader reads. Nothing is said by colour alone, and text meets
  WCAG 2.2 AA contrast. Held by review until the page's tests hold it.
- **The page has budgets.** Input is answered within 100 ms, and a run shows its first event
  within 1 s of Run. A viewer draws within a budget of points and memory, kept in code and named
  in the spec that adds it. Held by review until the page's tests hold it.
- **Versions.** Latest stable, pinned exactly. A runtime pins the newest release its family's code
  and wheels support, backed by a bench report. Every exception is in `versions.json` with its
  reason and review date ([docs/versions.md](docs/versions.md)). Held by `npm run versions`.
- **One data root.** Everything the app, the engine, a runtime child or a tool writes goes under
  the data root or the model store, at a path `oneframe/layout.py` names (`app/main/paths.js`
  mirrors it; `contracts/layout.json` holds the two equal). The only exceptions are its `outside`
  entries, and a new one is the owner's. Held by `test_ac2_nothing_is_written_outside_the_root`.
- **Kept files say their format.** A newer one is never overwritten, and a change of format ships
  with its migration. Held by the tests in `engine/tests/test_formats.py`.
- **Deletes stay inside the root or the store,** in folders the layout names. They never touch the
  person's own work unless the person asks for that one thing by name. Held by
  `test_remove_deletes_only_that_runtime` and
  `test_removing_a_checkpoint_deletes_only_its_files_and_partials`.
- **Separate roots.** A dev checkout and a packaged app never share one. Held by "the packaged app
  and a dev checkout get different roots, and so different locks".
- **Honest labels.** `trust` on an output is measured, predicted or synthetic, and a generated
  result is never marked measured. Held by `test_trust_is_inherited_as_the_weakest_input`.
- **`encoding="utf-8"` on every text open in Python;** Windows defaults to cp1252. Held by review.
