# 017: The first page: a graph to change, and two results side by side

Status: draft
Owner approval: (date, once approved)

The last of four specs for step 3, first light: [005](../005-first-models/spec.md) (the model
nodes), [015](../015-graphs/spec.md) (graphs as data, what fits here), [016](../016-door/spec.md)
(photos and results through the door), and this one, which needs all three. It is the editor's
first shape, not the editor: step 7 grows a canvas and the splat viewer from it.

## Problem

The page lists nodes and nothing else. A person cannot open a graph, change a setting, pick a photo,
run it, or see what it made; and when a run needs a runtime or files, nothing on the page offers to
install or download them. Nothing starts the app in CI either, so no test sees the page work.

## Requirements

1. **Open, change and save a graph.** The page opens a shipped or saved graph (015), shows each of
   its nodes as a card, and saves the person's graph, by name or as a new one.
2. **Settings come from manifests.** Each param is drawn from its manifest type (`int` and
   `float` within their bounds, `bool`, `choice`, `string`, and `file` as "Pick a photo", which
   asks `photo.pick`). A value the person set is marked as set, which the fit never changes, and
   can be reset to its default.
3. **Wiring by what fits.** Each input shows what feeds it. Connecting one lists what fits there
   from `graph.fits`, a converter named where one is needed; a node is added from the catalogue,
   filtered to what fits a chosen port, and a node or an edge can be removed.
4. **Run and Stop, and what happened.** Each card shows its node's state from the run's events:
   the fit (device, precision, what the fit changed, its warning), progress, cached, done with its
   seconds, or failed with its message and `next`.
5. **What a run needs is one press away.** A failure of kind `runtime`, reason `not_installed` or
   `out_of_date`, offers Install (`runtimes.install`, with its progress); one of kind `files`
   offers Download (`models.download`, with its progress); each then offers Run again.
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
- [ ] AC3: The node test "a failure offers what it needs" (`page.test.js`): each kind and reason
  of architecture.md's table maps to Install, Download, Run again or nothing, as requirement 5 says,
  and a kind the page does not know offers nothing and shows the message.
- [ ] AC4: The Playwright test "the first page runs a graph and shows two results"
  (`tests/e2e/first-page.test.js`), in CI on Windows and Ubuntu: the app starts on an empty data
  root with test nodes and a test graph from `tests/app/fixtures/` (engine nodes, no torch), the
  dialog answers a fixture photo, a setting is changed, Run gives two panes, each with a viewer
  showing a value fetched through `oneframe-value://`. Fails today: no such page.
- [ ] AC5: The same Playwright file, "Stop ends a run and the page says so": a test node that
  waits is stopped from the page, and its card says stopped within 5 s.
- [ ] AC6: `test_the_page_names_nothing` (`test_rules.py`, beside spec 013's rule): no file under
  `app/renderer/` holds a repo node's, graph's or runtime's id. Fails if the page hard-codes one.
- [ ] AC7 (hardware): on the owner's PC (Windows, GTX 1070), from an empty data root, the shipped
  graph opened from the page, a photo picked, the runtimes installed and the files downloaded from
  the page's own offers, and a run that shows Depth Pro's and MoGe's point clouds side by side. A
  report under `specs/017-first-page/reports/` gives each node's seconds, its fit, peak VRAM, and
  a screenshot.

## Design

**`app/renderer/`** grows from one file into modules, each a pure model beside the code that draws
it, so the node tests can import the models without a browser: `graph-model.js` (the open graph,
edits, the set/default marks), `form.js` (a control per param type), `run-view.js` (a card's state
from events, and AC3's offers), `npz.js` (a reader of numpy's `.npz`: a zip of `.npy`, stored or
deflated through `DecompressionStream`), and `viewers/` (one module per port type, chosen by the
type's name from `ports.list`, never a node's). The point cloud is drawn with three.js, a new
dependency loaded from `node_modules` (open question 2); the false colour is a fixed perceptual
map in code. `index.html` keeps its CSP, plus 016's scheme.

**The layout:** the catalogue on the left; the graph's cards in the middle; the panes on the right,
side by side, one per shown output. A pane's place, and which outputs are shown, are `page` fields
of the graph (015), so a saved graph opens as it was left.

**`tests/e2e/`** (new) uses Playwright's Electron support (`@playwright/test`, a new dev
dependency at its latest release). It stubs `dialog.showOpenDialog` from the test, in the main
process, so the app has no test hook. CI runs it after the app checks, under `xvfb-run` on Ubuntu,
with `ONEFRAME_NO_SANDBOX=1` only where the runner is root, as today.

**The shipped graph** (015's `graphs/first-light.json`) shows both nodes' point clouds; its `page`
fields are written by this page and committed once.

Risks: three.js through a relative path will not survive packaging (step 8 bundles it); a large
point map (Depth Pro at 1536² is 2.4 million points) is slow to draw (the viewer decimates to a
budget, kept in code); the e2e test is flaky on a slow runner (its waits are on events, with
deadlines that name what they waited for, as spec 014 requires).

## Tests

- Added: in `tests/app/page.test.js` (new), "a settings form is drawn from a manifest", "the npz
  reader reads what numpy writes", "a failure offers what it needs"; in
  `tests/e2e/first-page.test.js` (new), "the first page runs a graph and shows two results", "Stop
  ends a run and the page says so"; `test_the_page_names_nothing` (`test_rules.py`).
- Removed: none
- Renamed: none

## Verification

AC1 to AC6 are tests in `npm run check` (the e2e file as `npm run e2e`, which CI runs, and which
`npm run check` does not, since it starts Electron), in CI on Windows and Ubuntu.

AC7, in a local session on the owner's PC, from the branch, with a new empty folder:

1. `set ONEFRAME_DATA=<new empty folder>` then `npm start`.
2. Open "first light", pick a photo, press Run; press Install and Download as the page offers;
   Run again until both panes show their point clouds.
3. `npm run diagnose`, and write `specs/017-first-page/reports/<date>-local.md` from the journal:
   each node's seconds, fit and peak, the machine, and the screenshot beside it.

## Decisions taken

- Cards in a column, not a canvas: the canvas is step 7's, and the cards are what it will draw.
- The page's models are plain modules tested with `node --test`, so most of the page is checked
  without a browser; Playwright checks only that the pieces meet.
- The dialog is stubbed from the test, so the app ships no test switch.
- Linked cameras across point panes: the point of side by side is to compare one view.

## Out of scope

- A canvas, the splat viewer, wiring aids beyond "what fits here": step 7.
- Settings, the model store's list and removal, and runtime removal on the page: later pages.
- Packaging three.js: step 8.

## Open questions

1. **Two variants side by side** are two branches of one graph, each output the person chooses to
   show in its own pane. Recommended: yes; comparing two graphs is step 7's.
2. **three.js** (MIT) for the point viewer, the viewer step 7 extends with Spark. Recommended: yes,
   at its latest release, pinned exactly.
