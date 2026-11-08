# 015: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **The page's fields, and a graph's shape.** `Graph` and `GraphNode` keep a `page` object;
  `from_json` checks the graph's shape and reports each problem as a `GraphError` instead of a
  `KeyError`. AC1.
- [x] 2. **One `connect`.** `plan()`'s converter search becomes `connect()`, which answers
  `direct`, `via`, `depends` or `no`; the plan lists the edges it accepts pending a value's facets;
  `fits()` answers every opposite port of the graph and of the registry, an input already fed and
  an edge that closes a cycle included. AC4.
- [x] 3. **Graphs as files.** `oneframe/graphs.py`: `discover`, `get`, `save`, `remove`; one name
  check, `reading.check_id` and the store's device names moved into `reading`; the reason
  `graph`/`newer_format`; a `graphs` path in `layout.py`, `paths.js` and `contracts/layout.json`.
  AC2, AC3.
- [x] 4. **On the protocol.** `graphs.list`, `graphs.get`, `graphs.save`, `graphs.remove` and
  `graph.fits` on the server; `--graphs` names the shipped folders, the repo's `graphs/` by
  default. AC5.
- [x] 5. **The graph that ships.** `graphs/first-light.json`: the photo, Depth Pro to points, MoGe-3
  beside it. AC6.
- [ ] 6. **A failure stops only what depends on it.** The scheduler goes on past a failed step;
  `node.skipped` for each step that waited on it; `run.failed` lists the failed and the skipped.
  AC7.
- [ ] 7. **Docs and the end.** architecture.md (graphs as data, the five methods, the new reason,
  `node.skipped` and the run's ending), the handoff, `/code-review`, and the spec marked done in the
  PR.
