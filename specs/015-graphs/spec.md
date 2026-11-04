# 015: Graphs as data, and what fits here

Status: draft
Owner approval: (date, once approved)

The second of four specs for step 3, first light, which is too large for one: [005](../005-first-models/spec.md)
(the first model nodes), this one, [016](../016-door/spec.md) (photos and results through the door)
and [017](../017-first-page/spec.md) (the page). From the [review of 2026-10-12](../../docs/reviews/2026-10-12.md),
section 2 ("there is no home for a pipeline") and section 5, item 5; and the [roadmap](../../docs/roadmap.md)'s
wiring aids, layer 1. It needs none of the others, and 017 needs it.

## Problem

- A graph is JSON with a version (`oneframe/graph.py`), but nothing ships one, finds one or saves
  one, so the page cannot offer "photo → Depth Pro → points" without naming nodes in code.
- `Graph.from_json` drops every key it does not know, so a page that lays a graph out (a node's
  place, which outputs are shown) loses its own fields on the first round trip.
- To wire a graph the page must know which ports can connect. The rule (`ports.mismatch`, and the
  converter `plan()` may insert) lives in the engine alone, and the page would have to copy it.

## Requirements

1. **The page's own fields are kept.** A graph, and each node in it, may carry a `page` object
   the engine never reads, kept exactly through every read and write (open question 1).
2. **Graphs are found by looking**, as nodes are: every `*.json` in the folders of graphs the
   engine is given. The repo's `graphs/` holds the ones that ship; the data root's `graphs/` holds
   the person's. A broken file is listed as a problem and hides no other.
3. **A person saves, opens and removes their own graphs**; a shipped graph is never changed, and
   saving one saves the person's copy. A saved graph names its format; a newer one is never
   overwritten.
4. **What fits here is the engine's answer.** Given a graph and one port in it, the engine says,
   for each port of each node in the graph and each node it could add, whether it connects
   directly, through a named converter, or not, and why not, by the same rule `plan()` uses.
5. **One graph ships**, as a starting point: a photo, Depth Pro and MoGe side by side, each to
   points. Nothing in the engine or its tests reads it; tests build their own graphs.

## Acceptance criteria

- [ ] AC1: `test_page_fields_survive_a_round_trip` (`test_graph.py`): a graph whose top level and
  nodes carry `page` objects (nested, with lists and numbers) reads and writes back byte-equal in
  JSON terms. Fails today: `from_json` drops `page`.
- [ ] AC2: `test_graphs_are_found_by_looking` (`test_graphs.py`): two folders, one with two good
  graphs and one unparseable file, list both good ones with their source (`shipped` or `saved`)
  and one problem naming the bad file. Fails today: no module finds graphs.
- [ ] AC3: `test_saving_keeps_shipped_graphs_and_newer_formats` (`test_graphs.py`): saving under
  a shipped graph's name writes the person's copy and leaves the shipped file unchanged; saving
  over a saved graph whose `version` is newer is refused with kind `graph`, reason
  `newer_format`, and the file is unchanged.
- [ ] AC4: `test_what_fits_agrees_with_plan` (`test_graph.py`): over a registry of test nodes that
  includes a converter, for every pair of an output and an input, `fits()` answers `direct` exactly
  when `plan()` accepts the edge with no converter, `via` (with the converter's id) exactly when
  `plan()` inserts that converter, and `no` (with `plan()`'s reason) exactly when it refuses.
- [ ] AC5: `test_graph_methods_over_the_protocol` (`test_server_and_nodes.py`): through the
  NDJSON server, `graphs.save`, `graphs.list`, `graphs.get`, `graph.fits` and `graphs.remove`
  answer as AC2 to AC4 say, and `graphs.remove` of a shipped graph is refused with
  `request`/`bad_params`. Fails today: no such methods.
- [ ] AC6: `test_shipped_graphs_plan` (`test_graphs.py`): every graph in the repo's `graphs/`
  plans against the repo's nodes with no problem. Runs once 005's nodes exist; until then the
  shipped graph does not exist either.

## Design

**`oneframe/graph.py`.** `Graph` and `GraphNode` gain `page: dict[str, Any] | None`, read and written
as they are (open question 1). The converter search inside `plan()` moves to one function,
`connect(out_spec, in_spec, converters) -> Connection` (`direct`, `via` a converter, or `no` with
the reason); `plan()` and `fits()` both call it, so the two cannot disagree. `fits(graph, port,
registry)` takes `node.port`, either an output or an input, and answers every opposite port of the
graph's other nodes and of every registered node, each with its `connect` result, and for an input
already fed, that it is taken.

**`oneframe/graphs.py`** (new): `discover(roots)` through `reading.discover`, each root marked
`shipped` or `saved`; `save(root, name, graph)` through `files.write_json(..., key="version")`, which
already refuses a newer format; `remove(root, name)`, inside the saved root only. A graph's name is
its file's stem, checked by `reading.check_id`.

**`oneframe/layout.py`, `app/main/paths.js`, `contracts/layout.json`:** a `graphs` path,
`graphs`, under the root. A new folder, not a change of format.

**`oneframe/server.py`:** `graphs.list`, `graphs.get {name}`, `graphs.save {name, graph}`,
`graphs.remove {name}` and `graph.fits {graph, port}`. The engine is given its shipped folders by
`--graphs`, as it is given node folders by `--nodes` (the repo's `graphs/` by default), and finds
the person's under the root; a test passes its own. These methods join `ENGINE_METHODS` in 016,
with the door's photo tokens.

**`graphs/first-light.json`** lands with 005's nodes: `source.image` feeding the Depth Pro node
and the MoGe node, each feeding `convert.depth_to_points` or giving points directly, as their ports
say, with `page` fields that place the two results side by side (017 reads them).

Risks: a `page` object grows without bound (the page's concern; the engine only keeps it); two
engines on one root save the same name at once (the writer is atomic, so the last one wins whole).

## Tests

- Added: `test_page_fields_survive_a_round_trip`, `test_what_fits_agrees_with_plan` (`test_graph.py`);
  `test_graphs_are_found_by_looking`, `test_saving_keeps_shipped_graphs_and_newer_formats`,
  `test_shipped_graphs_plan` (`test_graphs.py`); `test_graph_methods_over_the_protocol`
  (`test_server_and_nodes.py`); the layout row for `graphs` in `contracts/layout.json`, which both
  suites check.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run engine:check`, in CI on Windows and Ubuntu. No hardware step.

## Decisions taken

- The recipe stays version 1: `page` is a new optional field, and no older app saves graphs, so
  none can damage one (architecture: a format goes up only when an older app would misread it).
- Graphs are files named by their stem, not ids inside the file: a person sees them in a folder.
- `fits` answers every registered node, not only those in the graph, so the page can offer "add a
  node that fits here" with the same call.
- One `connect` for `plan()` and `fits()`, under "one way to do each thing".

## Out of scope

- Reaching these methods from the page, and photo paths inside a saved graph: 016.
- The page that edits, saves and runs a graph: 017.
- Wiring aids beyond layer 1 (auto-wiring, a goal search): the editor, step 7.

## Open questions

1. **The page's fields** are kept as a `page` object on the graph and on each node, opaque to the
   engine. A saved graph is a file a person keeps, so its shape is hard to change. Recommended:
   yes, under that one key, so nothing the page adds can collide with what the engine reads.
