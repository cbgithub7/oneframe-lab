# 021: Collections and "for each"

Status: draft
Owner approval: (date, once approved)

The third of five specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), [020](../020-structured-params/spec.md) (data a person authors), this one,
[022](../022-run-to-a-node/spec.md) (a run up to a node) and [023](../023-runs-at-once/spec.md)
(several runs at once). From the [review of 2026-11-04](../../docs/reviews/2026-11-04.md), section
1, item 3. It lands after 019 (the key, `meta.sources`) and 020 (a JSON value's shape).

## Problem

A port carries one value, and a step runs once. Step 4 turns one photo into several objects: a
segmenter gives N masks, and each mask is to become a mesh by the same nodes. Today that needs a
graph with N copies of those nodes, which nobody can build before the masks exist. `ViewSet` is a
folder of views, but a type of its own: nothing can take it apart or run a node on each view.

## Requirements

1. **A port may carry a collection** of its type (open question 1): written `Mask[kind=binary]*`.
   A collection is an ordered list of values, each with its own facets, meta, trust and key; it may
   be empty.
2. **A node gives and takes one.** Its code declares a collection output item by item, in order,
   and reads a collection input as a list. Every item is checked as a single value is. The
   collections one step gives line up, item for item, and the engine holds them to one length.
3. **For each.** An edge from a collection output to a single input of its type is legal, and the
   step it feeds then runs once for each item, in order. Each of that step's outputs is a
   collection in the same order. A single value on another of its inputs is given to every run.
4. **Collections line up or the plan says so.** A step fed several collections item by item runs
   only when they come from one step, directly or through steps run for each of them. Collections
   from two steps fed so, a single value into a collection input, and a for each inside another are
   each refused when the graph is planned, with the problem named.
5. **Each item is its own work.** A run for one item is cached under that item's key, so adding or
   changing an item re-runs only that item, and each run is fitted to memory as a step is today.
6. **A collection input takes the whole,** which ends a for each: a node that places every mesh in
   one scene declares `meshes: Mesh*`.
7. **A failed item fails its step** (open question 2): the items after it do not start, the items
   done stay cached, and what depends on the step is skipped, as today. An item whose facets do
   not fit the input it reaches skips the step, as a single value's do, naming the item.
8. **An empty collection runs nothing and says so:** the step is done, with no run, and gives
   empty collections.
9. **`graph.fits` answers `each`** where the plan would run for each, by the one rule `connect()`.
10. **The page shows a collection:** the number of items on the card, and one item at a time in a
    pane, the next and the previous reachable from the keyboard; each item is read by its own key.

## Acceptance criteria

- [ ] AC1: `test_a_node_gives_and_takes_a_collection` (`test_scheduler.py`): a test node declares
  three items; a node whose input is a collection receives the three in order, each with its own
  facets and a key of its own.
- [ ] AC2: `test_a_single_input_fed_a_collection_runs_for_each` (`test_scheduler.py`): a step fed
  three items and one single value runs three times, each with one item and that value, each run's
  events carrying its `item` and `of`; a node that takes the whole then receives the three results
  in the items' order.
- [ ] AC3: `test_for_each_reruns_only_the_items_that_changed` (`test_scheduler.py`): after a run
  over three items, a run over those three and a fourth starts the step once, for the fourth, and
  answers the other three from the cache.
- [ ] AC4: `test_plan_refuses_collections_that_do_not_line_up` (`test_graph.py`): collections from
  two steps fed item by item into one, a single value into a collection input, and a for each over
  a node that itself gives a collection are each a problem that names the node and port.
- [ ] AC5: `test_a_failed_item_fails_its_step` (`test_scheduler.py`): with the second of three
  items failing, the step ends `node.failed` naming item 1, the third never starts, a dependent
  step is skipped, and a second run answers the first item from the cache.
- [ ] AC6: `test_an_empty_collection_runs_nothing_and_says_so` (`test_scheduler.py`): a for each
  over no items starts nothing, says `node.done` with `of` 0, hands a node that takes the whole an
  empty list, and the run ends done.
- [ ] AC7: `test_what_fits_says_each` (`test_graph.py`): for every pair of an output and an input
  over test nodes with collection ports and a converter, `fits()` answers `each` (direct or through
  the converter) exactly where `plan()` runs the step for each.
- [ ] AC8: "a pane shows a collection one item at a time" (`tests/e2e/first-page.test.js`, in CI):
  with a test node that makes three images, its card says three items, its pane shows the first,
  read by that item's key, and the pane's Next, pressed from the keyboard, shows the second.

## Design

**Ports** (`ports.py`): the spec string takes a trailing `*`, in either form of a port; `PortSpec`
gains `many`. `mismatch` compares the item types as today. The manifest check refuses `*` on a
`ViewSet`. The page's `typeName` (`graph-model.js`) reads it too.

**The one rule** (`graph.py`): `connect()` strips the `*` and applies today's rule, then says how
the two ends meet: single to single and collection to collection as today; collection to single
adds `each`; single to collection is `no`. A converter the plan inserts on an `each` edge is fed
the collection, so it runs for each too. `plan()` marks a step `each` when an input is fed so, and
gives every collection an origin: the step whose node made it, which the outputs of a step run for
each of it inherit. A node that takes a whole collection and gives another starts a new origin.
Only inputs fed `each` must share an origin; a whole collection from elsewhere on another input is
given to every run, as a single value is. The plan's notes say which steps run for each.

**Values.** A collection is `list[Value]`. An item a node made has the key
`<entry>:<port>:<index>`; an item of a step run for each keeps the key its own run's entry gives it
(`<entry>:<port>`), so that collection is a list of references, stored nowhere again, and the step
has no entry of its own. `Cache.commit` and `get` write and read a collection port's rows as a list
(`outputs.json`, which only entries under 019's key version hold). A key takes an input collection
as the list of its items' keys. `values.locate` answers an item when the port names one
(`masks:0`), so the value protocol and the page's `valueUrl` read an item's key as they read any.

**The node API** (`oneframe/node/`): `ctx.output_item(port, path, facets, meta)` declares the next
item of a collection output, and a collection port with none declared is empty; `ctx.items(port)`
gives a collection input as a list. `ctx.inputs` holds single inputs only and keeps its type, so no
node written today changes. `ctx.output` on a collection port, `output_item` on a single one, and
two collection outputs of unequal length are the node's `contract` failure. docs/nodes.md gains
them.

**The scheduler** (`scheduler.py`): for an `each` step, `_steps` gathers the items, then runs the
step once per item with that item's inputs: the cache, the fit, the files check, the one retry
after `oom` and the events are a single step's, each event carrying `item` (from 0) and `of`. The
step is done when its last item is; `run.done`'s outputs hold its collections. A collection input
taken whole gives a memory model one factor, `<input>.count`. `bench:fit --graph` refuses a step
that runs for each.

**The protocol** (`contracts/protocol.json`): a `collection` shape (`type`, `items`: values); a
run's outputs are a value or a collection; the node events gain optional `item` and `of`;
`connection` gains `each`. The door strips a collection's items of their paths as it strips a
value's.

**The page** (`run-view.js`, `viewer.js`, `main.js`): a card counts a step's items done of `of`
from the events and is done at the last; it keeps one state a step, never one an item, and redraws
at most once a frame however many events arrive. A pane on a collection holds an index in the
graph's `page` fields, with Previous and Next buttons that name the item ("Item 2 of 3").

**Docs:** architecture.md (words: collection, for each; Events), nodes.md, and the handoff.

Risks: the page's card reads the first `node.done` as the step's end (a test of `run-view.js` runs
three); automatic segmentation gives 50 to 200 items, each with its events (the card's one state
and one redraw a frame are the bound, and a node's own `progress` is already throttled).

## Tests

- Added: the tests the criteria name; `test_a_collection_port_is_read_and_written`
  (`test_ports_and_manifests.py`); `test_an_item_is_checked_as_a_single_value_is` and
  `test_collections_of_one_step_have_one_length` (`test_scheduler.py`);
  `test_an_item_is_located_by_its_key` (`test_server_and_nodes.py`); "a card counts the items of a
  for each" (`tests/app/page.test.js`).
- Changed: "nothing the page receives holds a path" (`tests/app/door.test.js`) gains a collection.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run engine:check` or `npm run e2e`, in CI on Windows and Ubuntu.
No hardware step: the first real for each is step 4's, with its own report.

## Decisions taken

- For each follows from the types, with no field in the graph: the recipe stays version 1, and
  `connect()` stays the one rule for `plan()` and `fits()`. Open question 1 asks the owner.
- Collections line up by the step that made them, never as a product of two: a product is rarely
  meant, and a graph that wants one can say so with a node.
- The collections one node gives have one length: a segmenter's masks and boxes then line up
  without a word in its manifest. A node that needs two lengths waits for a manifest that says so.
- `ctx.items`, not a list inside `ctx.inputs`: the node contract of spec 009 stays as it is.
- A step run for each has no cache entry of its own: its items' entries are the work.
- One event stream with `item` and `of`, no new events: the page's card logic gains a count.
- `ViewSet` stays a type: its views share one subject and one record of how they were made.

## Out of scope

- A node that takes a `ViewSet` apart or builds one from a collection: with the Improve loop.
- Running the items of one step at once: 023 gives each item's run its device's slot in turn.
- A for each inside a for each, and choosing or reordering items in the page: the editor, step 7.

## Open questions

1. **How a graph says "for each".** The review asked for it "in the recipe". Recommended: it
   follows from the types, as a converter does: an edge from `Mask*` to a `Mask` input runs that
   step for each, the plan and the page say so, and nothing new is saved in a graph. The other way
   is a field on the node in the graph, which a person sets and the plan checks against the types:
   more to author, and a second way to be wrong. The `*` in a manifest's port is a format node
   authors write; recommended as shown.
2. **One failed item** fails its step, and the items done stay cached, so Run again goes on from
   there. The other way keeps going past a failed item and gives collections with gaps, which
   every later node must then understand. Recommended: fail the step.
