# 021: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **A collection port.** `PortSpec.many`, read from a trailing `*` in either form and
  written back by `text()`; `mismatch` compares the item types; the manifest refuses `*` on a type
  carried as a folder; the page's `typeName` keeps it. `test_a_collection_port_is_read_and_written`.
- [x] 2. **The plan and what fits.** `connect()` says `each`; `plan()` marks a step run for each,
  gives each collection its origin, refuses what does not line up, and says so in its notes;
  `fits()` answers `each` by the same rule; `connection` gains `each` (`contracts/protocol.json`,
  `app/protocol.d.ts`). AC4, AC7.
- [x] 3. **A collection as values.** `ctx.output_item` and `ctx.items`, a job's collection inputs;
  `outputs.py` checks each item as a single value and holds a step's collections to one length;
  the cache writes and reads a collection port's rows as a list, and a key takes a collection as its
  items' keys; `values.locate` answers an item (`masks:0`); the protocol's `collection` shape, and a
  run's outputs as a value or a collection; a collection input gives the fit `<input>.count`. AC1,
  `test_an_item_is_checked_as_a_single_value_is`, `test_collections_of_one_step_have_one_length`,
  `test_an_item_is_located_by_its_key`.
- [ ] 4. **For each in the scheduler.** A step run for each runs once an item, each run a single
  step's (the cache, the fit, the files check, the retry, the events, with `item` and `of`); a
  failed item fails its step; an empty collection runs nothing and says `node.done` with `of` 0;
  `bench:fit --graph` refuses a step run for each. AC2, AC3, AC5, AC6.
- [ ] 5. **The door and the page.** The door strips a collection's items of their paths; a card
  counts a step's items and keeps one state a step; the page redraws at most once a frame; a pane on
  a collection shows one item, with Previous and Next, its index in the graph's `page` fields.
  "a card counts the items of a for each", "nothing the page receives holds a path" with a
  collection.
- [ ] 6. **The e2e test.** A fixture node that makes three images, a graph that shows them, and
  "a pane shows a collection one item at a time". AC8.
- [ ] 7. **Docs and the end.** architecture.md (collection, for each; Events), nodes.md, the
  handoff; the full checks; `/code-review`; the spec marked done in the PR.
