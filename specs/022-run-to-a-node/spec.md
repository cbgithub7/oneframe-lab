# 022: A run up to a node

Status: draft
Owner approval: (date, once approved)

The fourth of five specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), [020](../020-structured-params/spec.md) (data a person authors),
[021](../021-collections/spec.md) (collections and "for each"), this one, and
[023](../023-runs-at-once/spec.md) (several runs at once). From the
[review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 1, item 2, and the
[review of 2026-11-07](../../docs/reviews/2026-11-07.md), section 2, finding 3 (one graph walk). It
needs none of 019 to 021, and is placed after them only because the page work of each builds on
the last.

## Problem

- **A run is always the whole graph.** To see the masks a click gives, a person runs everything
  after the segmenter too, an object generator of minutes among it.
- **A graph being built cannot run.** One input still unconnected anywhere refuses the whole run
  (`plan()`), so nothing can be looked at until every node is wired.
- **The graph is walked in two places:** `graph.py` (`_reaches`, for `fits`) and `bench_fit.py`
  (`upstream_of`, with `part_of` to cut the graph down), which differ in whether a step reaches
  itself.

## Requirements

1. **A run may name the steps it is for** (`graph.run {graph, upto}`): those steps and what they
   depend on run, in the order the whole graph would run them. Without `upto` the whole graph
   runs, as today.
2. **Nothing else is planned.** A problem in a step the run does not need (an input not connected,
   a node that is not installed, a photo not picked) does not refuse it. A step in `upto` that the
   graph does not have is refused, by name.
3. **`graph.validate` takes `upto` too,** so a caller can ask whether a part would run.
4. **The page offers "Run to here"** on each card, reachable from the keyboard and named for a
   screen reader with the card's title. The cards a run does not hold keep what they showed.
5. **One graph walk,** in `graph.py`, which `fits`, `upto` and `bench:fit --graph` all use.

## Acceptance criteria

- [ ] AC1: `test_a_run_up_to_a_step_runs_only_what_it_needs` (`test_scheduler.py`): in a graph of
  two branches from one source, a run up to one branch's last step says that branch's steps as
  its order, ends done with their outputs, and never names the other branch. Fails today.
- [ ] AC2: `test_a_problem_elsewhere_does_not_refuse_a_run_up_to_a_step` (`test_graph.py`): with a
  third node whose input is not connected, `plan()` of the whole graph is refused and `plan()` up
  to a step that does not need that node is not.
- [ ] AC3: `test_upto_names_steps_the_graph_has` (`test_server_and_nodes.py`): through the server,
  `graph.run` and `graph.validate` with an `upto` step the graph does not have are refused with
  kind `graph`, the problem naming the step.
- [ ] AC4: `test_one_walk_serves_fits_upto_and_the_bench` (`test_rules.py`): `bench_fit.py` defines
  no function that reads a graph's edges, which `graph.py`'s walk answers for it. Fails today.
- [ ] AC5: "Run to here runs up to a card" (`tests/e2e/first-page.test.js`, in CI): Run to here,
  pressed from the keyboard on a card in the middle of a graph, ends with that card done and the
  card after it never started.

## Design

**The walk** (`graph.py`): `_reaches` becomes public, `reaches(graph, start, backwards)`, the start
included as today, and `Graph.part(keys)` gives the nodes in `keys` and the edges between them.
`bench_fit.py`'s `upstream_of` and `part_of` go; its callers use these, taking the measured step
out of what it reaches where they need only what it depends on.

**The plan** (`graph.py`): `plan(graph, registry, imports, upto=None)`. With `upto`, each name must
be a node of the graph (a problem otherwise), and the plan is of `graph.part(...)`: what the named
steps reach backwards. Every check then runs on the part alone, so the rest of the graph is not
read. A converter the plan inserts cannot be named: its id is not a node of the graph. With 021, a
step run for each is named as one step, and runs for every item.

**The scheduler and the server** (`scheduler.py`, `server.py`): `Scheduler.run` and `graph.run`
pass `upto` to the plan; `graph.validate` does the same. Nothing else changes: `run.start`'s order
is the part's, `run.done`'s outputs the part's, and the runtimes a run is said to use are the
part's.

**The protocol** (`contracts/protocol.json`): `graph.run` and `graph.validate` take an optional
`upto`, a list of step names. No new event.

**The page** (`main.js`, `run-view.js`): every card has a "Run to here" button beside its title,
labelled "Run to here: <the card's title>". It asks `graph.run` with that card's step. The run view
resets only the cards the run's `run.start` names, so the others keep their last state and their
panes. Run, as today, runs the whole graph and still names every problem in it.

**Docs:** architecture.md (Events: `upto`), nodes.md's bench section, and the handoff.

Risks: a person takes a part's "done" for the graph's (the cards outside the run keep their own
state and say when it is from an earlier run); the bench's two callers differ on whether a step
reaches itself (the existing `bench:fit --graph` tests hold both).

## Tests

- Added: the tests the criteria name; "a run to a card resets only its cards"
  (`tests/app/page.test.js`).
- Changed: none
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run engine:check` or `npm run e2e`, in CI on Windows and Ubuntu.
No hardware step.

## Decisions taken

- `upto` plans only the part: a graph being built runs as far as it is built, and the whole graph
  is still checked by Run.
- `upto` is a list, though the page sends one step: a caller that wants two results asks once.
- No "from here on": the cache already skips what is made, so a run of the whole graph after a run
  to a node repeats nothing.

## Out of scope

- A second run while one goes: 023. Until then Run to here is refused `busy` while a run goes, as
  Run is.
- Showing what the cache holds for a card without running ("show what is there"): the editor,
  step 7.
- Running on every edit: the editor, step 7, once 023 and spec 004 make it cheap.

## Open questions

None. What the page offers here was agreed with the step (the review of 2026-11-04, decision 7:
"runs up to a chosen node").
