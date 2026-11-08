# 017: The first page: a graph to change, and two results side by side

Status: draft
Owner approval: (date, once approved)

The last of five specs for step 3, first light: [005](../005-first-models/spec.md) (the model
nodes), [015](../015-graphs/spec.md) (graphs as data, what fits here), [016](../016-door/spec.md)
(photos and results through the door), [018](../018-protocol/spec.md) (the protocol, declared),
and this one, which needs all four. It is the editor's first shape, not the editor: step 7 grows a
canvas and the splat viewer from it.

## Problem

The page lists nodes and nothing else. A person cannot open a graph, change a setting, pick a photo,
run it, or see what it made; and when a run needs a runtime or files, nothing on the page offers to
install or download them. Nothing starts the app in CI either, so no test sees the page work.

## Requirements

1. **Open, change and save a graph.** The page opens a shipped or saved graph (015), shows each of
   its nodes as a card, and saves the person's graph, by name or as a new one.
2. **Settings come from manifests.** Each param is drawn from its manifest type (`int` and
   `float` within their bounds, `bool`, `choice`, `string`, and `file` as "Pick a photo", which
   asks `photo.pick` and shows the import's name; v1's only file params are images). A value the person set is marked as set, which the fit never changes, and
   can be reset to its default.
3. **Wiring by what fits.** Each input shows what feeds it. Connecting one lists what fits there
   from `graph.fits`, a converter named where one is needed; a node is added from the catalogue,
   filtered to what fits a chosen port, and a node or an edge can be removed.
4. **Run and Stop, and what happened.** Each card shows its node's state from the run's events:
   the fit (device, precision, what the fit changed, its warning), progress, cached, done with its
   seconds, or failed with its message and `next`.
5. **What a run needs is one press away.** A failure's kind and reason decide the offer:
   `runtime`/`not_installed` or `out_of_date` offers Install (`runtimes.install`, with its
   progress); `files`/`not_downloaded` or `incomplete` offers Download (`models.download`, with its
   progress); `runtime`/`installing` and `files`/`downloading` wait for that job's ending; every
   other failure with `retry` true offers Run again; the rest show their message and `next`. Each
   finished Install or Download offers Run again.
6. **Viewers by port type, side by side.** Any output can be shown; the shown ones sit in panes
   side by side, kept in the graph's `page` fields. An `Image` is a picture; a `Depth` a false
   colour image with its range in metres or units; a `PointMap` a point cloud to orbit, with
   cameras linked across panes; a `Camera` its numbers; any other type its type, facets and meta.
   Every viewer reads its value through 016's protocol, by key.
7. **The page names nothing.** No node, graph, model or runtime id appears in the page's code; it
   learns everything from the engine.

## Acceptance criteria

- [ ] AC1: The node test "a settings form is drawn from a manifest" (`page.test.js`): for a test
  manifest with one param of each type, the form model has one control each with the manifest's
  bounds, choices and default; setting a value marks it set, and reset removes it from the graph.
- [ ] AC2: The node test "the npz reader reads what numpy writes" (`page.test.js`): `.npz` files
  written by numpy (float32 and bool arrays, stored and deflated) committed under
  `tests/app/fixtures/` read back to the same shapes and values.
- [ ] AC3: The node test "a failure offers what it needs" (`page.test.js`): every kind and reason
  of architecture.md's table, each with its `retry`, gets the offer requirement 5 gives it, and a
  kind the page does not know gets none.
- [ ] AC4: The Playwright test "the first page runs a graph and shows two results"
  (`tests/e2e/first-page.test.js`), in CI on Windows and Ubuntu: the app starts on an empty data
  root with test nodes and a test graph from `tests/app/fixtures/` (engine nodes, no torch), the
  dialog answers a fixture photo, Run gives two panes, and each viewer draws a value fetched
  through `oneframe-value://`. Fails today: no such page.
- [ ] AC5: The same Playwright file, "Stop ends a run and the page says so": a test node that
  waits is stopped from the page, and its card says stopped within 5 s.
- [ ] AC6: `test_the_page_names_nothing` (`test_rules.py`, beside spec 013's rule): no file under
  `app/renderer/` holds a repo node's, graph's or runtime's id. Fails if the page hard-codes one.
- [ ] AC7: The node test "every viewer is for a port type that exists" (`page.test.js`): each
  module in `app/renderer/viewers/` names a type in `contracts/port-types.json`.
- [ ] AC8 (hardware): on the owner's PC (Windows, GTX 1070), from an empty data root and a terminal
  outside Claude desktop's package: the shipped graph opened from the page, a photo picked, the
  runtimes installed and the files downloaded from the page's own offers, and a run that shows
  Depth Pro's and MoGe's point clouds side by side (`depth.moge3`, or `depth.moge2` if 005's open
  question 1 goes that way). A report under `specs/017-first-page/reports/` gives each node's
  seconds, its fit, peak VRAM, and a screenshot.

## Design

**`app/renderer/`** grows from one file into modules, each a pure model beside the code that draws
it, so the node tests can import the models without a browser: `graph-model.js` (the open graph,
edits, the set/default marks), `form.js` (a control per param type), `run-view.js` (a card's state
from events, and AC3's offers), `npz.js` (a reader of numpy's `.npz`: a zip of `.npy`, stored or
deflated through `DecompressionStream`), and `viewers/` (one module per port type, chosen by the
type's name from `ports.list`, never a node's). The point cloud is drawn with three.js and its
orbit controls (open question 2); the false colour is a fixed perceptual map in code. `run-view.js`
reads events typed from 018's `app/protocol.d.ts`.

**One bundle, no import map.** three's add-ons import the bare name `three`, which a browser
resolves only through an import map, an inline script the CSP would allow only by its hash. So
`npm run page:build` (esbuild, a new dev dependency at its latest release, pinned) bundles
`app/renderer/main.js` with three and its orbit controls into one `page.js`, beside copies of
`index.html` and `styles.css`, in `dist/renderer/` (ignored by git). `main.js` loads the page from
there, and `npm start` and `npm run e2e` build it first. The CSP stays `script-src 'self'`, with no
inline script, plus 016's scheme. tsc checks the sources, and the node tests import them unbundled.

**Types per environment.** One `tsconfig.json` gives every file both DOM and Node types today, so
page code could import `node:fs` and pass. It splits in three, each run by `npm run typecheck`: the
page's (`app/renderer/`: DOM, no Node), the preload's (`require` for Electron's renderer modules,
no DOM), and the rest's (main, scripts, hooks and tests: Node, no DOM). ESLint refuses `node:` and
`electron` imports under `app/renderer/`. The node tests import only the page's models, which use
neither, so the rest's config checks them too.

**The point viewer's budget,** which the page rule on budgets asks this spec to name: at most 1
million points drawn a pane (`POINT_BUDGET`), at the smallest whole stride over the map's rows and
columns that fits, and the pane says how many it drew of how many. A point map is the photo's size:
the owner's 7.2 MP photo gives 7.2 million points a pane, 14.4 million for two, drawn at a stride
of 3. A pane keeps only its drawn points (at most 12 MB of positions); the `.npz` it read, about
95 MB at 7.2 MP, is let go once they are on the card.

**The layout:** the catalogue on the left; the graph's cards in the middle; the panes on the right,
side by side, one per shown output. A pane's place, and which outputs are shown, are `page` fields
of the graph (015), so a saved graph opens as it was left.

**Folders of the person's own.** `app/main/paths.js`'s `engineCommand` passes each folder in
`ONEFRAME_NODES` and `ONEFRAME_GRAPHS` (lists, as `PATH` is) to the engine as `--nodes` and
`--graphs`, after the repo's: how a person tries a node of their own, and how the e2e test gives
its fixtures.

**`tests/e2e/`** (new) uses Playwright's Electron support (`@playwright/test`, a new dev
dependency at its latest release), run by a new `npm run e2e`. It replaces `dialog.showOpenDialog`
from the test, in the main process. CI runs it after the app checks, under `xvfb-run` on Ubuntu;
`ONEFRAME_NO_SANDBOX=1` only where the runner runs as root.

**Docs:** the commands list (`npm run e2e`, `npm run page:build`), architecture.md (the page's
modules, the bundle, the viewers), nodes.md (folders of the person's own).

**The shipped graph** (015's `graphs/first-light.json`) shows both nodes' point clouds; its `page`
fields are written by this page and committed once.

Risks: a built page, so a stale `dist/renderer/` could run (`npm start` builds it each time);
point maps of millions of points (the budget above); the e2e test is flaky on a slow runner (its
waits are on events, with deadlines that name what they waited for, as spec 014 requires).

## Tests

- Added: in `tests/app/page.test.js` (new), "a settings form is drawn from a manifest", "the npz
  reader reads what numpy writes", "a failure offers what it needs", "every viewer is for a port
  type that exists"; in `tests/e2e/first-page.test.js` (new), "the first page runs a graph and shows two results", "Stop
  ends a run and the page says so"; `test_the_page_names_nothing` (`test_rules.py`).
- Removed: none
- Renamed: none

## Verification

AC1 to AC3 and AC7 are node tests in `npm run check`; AC6 is in `npm run engine:check`; AC4 and AC5
run in `npm run e2e`, which starts Electron and so is not part of `npm run check`. All run in CI on
Windows and Ubuntu.

AC8, in a local session on the owner's PC, from the branch, with a new empty folder:

1. In a PowerShell window opened from the Start menu, not from Claude desktop, whose package
   redirects new AppData folders and makes runtime installs fail (os error 32, in the handoff):
   `$env:ONEFRAME_DATA = "<new empty folder>"`, then `npm start`.
2. Open "first light", pick a photo, press Run; press Install and Download as the page offers;
   Run again until both panes show their point clouds. With `precision` unset, as the shipped graph
   leaves it, the 1070 gets Depth Pro's fp32 on the processor (its 9.2 GB card estimate does not
   fit 8 GB): about 110 s on the 4000 x 1800 photo, its card showing the fit's choice and any
   warning, as 005's AC7 report measured. Then set fp16 and Run again: on the card, about 13 s.
   The report gives both runs.
3. `npm run diagnose`, and write `specs/017-first-page/reports/<date>-local.md` from the journal:
   each node's seconds, fit and peak, the machine, and the screenshot beside it.

## Decisions taken

- Cards in a column, not a canvas: the canvas is step 7's, and the cards are what it will draw.
- The page's models are plain modules tested with `node --test`, so most of the page is checked
  without a browser; Playwright checks only that the pieces meet.
- The dialog is replaced from the test; the node and graph folders the test gives are a feature a
  person uses too.
- Linked cameras across point panes: the point of side by side is to compare one view.
- esbuild, as the review of 2026-11-04 asks (section 5, item 3), not an import map: an inline map
  is re-hashed into the CSP at every change, and step 8 bundles anyway. Spark joins the same bundle
  at step 7, which settles open question 2's note on it.
- Three type configs, as the same review asks (section 2, item 4): a page that imports Node must
  fail `npm run check`, not pass because one config gives every file both.
- The bundle in `dist/renderer/`, ignored by git, as `node_modules` is: the build's output, not the
  app's; nothing the app writes goes there.
- One million points a pane: enough to read a scene's shape in a pane a few hundred pixels wide. A
  whole stride keeps the grid, so two panes of one photo draw the same pixels.

## Out of scope

- A canvas, the splat viewer, wiring aids beyond "what fits here": step 7.
- Settings, the model store's list and removal, and runtime removal on the page: later pages.
- Packaging the bundle: step 8.

## Open questions (answered by the owner, 2026-11-04: every one as recommended)

1. **Two variants side by side** are two branches of one graph, each output the person chooses to
   show in its own pane. Recommended: yes; comparing two graphs is step 7's.
2. **three.js** (MIT) for the point viewer, the viewer step 7 extends with Spark. Recommended: yes,
   at its latest release, pinned exactly. Without a bundler, Spark will need an import map, which
   the CSP allows only by hash; step 7 decides on a bundler.
3. **A run that meets a failure** stops today, so a missing runtime for one branch also stops the
   other. Recommended: in this spec, the scheduler keeps running every step that does not depend on
   the failed one, and the page shows each branch's own ending. The scheduler's part is spec
   015's (its requirement 6); the page shows `node.skipped` on a card.
