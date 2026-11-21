# 022: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **One graph walk.** `_reaches` becomes `reaches(graph, start, backwards)`; `Graph.part(keys)`
  gives the nodes in `keys` and the edges between them; `bench_fit.py`'s `upstream_of` and
  `part_of` go, its callers using these, and the sizes of the measured step's inputs read from its
  plan, not the graph's edges. AC4.
- [x] 2. **The plan up to steps.** `plan(..., upto=None)`: every name a node of the graph, or a
  problem naming it; the plan is of the part the names reach backwards, so a problem elsewhere does
  not refuse it. AC2.
- [x] 3. **The scheduler, the server and the protocol.** `Scheduler.run(..., upto=)`; `graph.run`
  and `graph.validate` take an optional `upto`, a list of step names (`contracts/protocol.json`,
  `app/protocol.d.ts`); `bench:fit --graph` runs what the step depends on by `upto`. AC1, AC3.
- [ ] 4. **The page.** "Run to here: <title>" on every card, asking `graph.run` with that step;
  `run.start` resets only the cards it names, and a card kept from an earlier run says so.
  "a run to a card resets only its cards" (`tests/app/page.test.js`).
- [ ] 5. **The e2e test.** A fixture graph of three steps in a line, and "Run to here runs up to a
  card". AC5.
- [ ] 6. **Docs and the end.** architecture.md (Events: `upto`), nodes.md's bench section, the
  handoff; the full checks; `/code-review`; the spec marked done in the PR.
