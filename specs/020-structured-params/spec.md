# 020: Data a person authors: structured params

Status: draft
Owner approval: (date, once approved)

The second of five specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), this one, [021](../021-collections/spec.md) (collections and "for each"),
[022](../022-run-to-a-node/spec.md) (a run up to a node) and [023](../023-runs-at-once/spec.md)
(several runs at once). From the [review of 2026-11-04](../../docs/reviews/2026-11-04.md), section
1, item 4, and section 2.6. It lands after 019, whose vocabulary contract holds the param types.

## Problem

- **The page cannot author data.** A param is a number, a switch, text, a choice or a file. Step
  4's first node needs clicks on the photo (SAM 2.1), and later steps a camera path's keys and an
  object's placement; none of these fits a param today.
- **The JSON port types have no shape.** `Clicks`, `Camera`, `Pose` and four more are carried as
  JSON, and the contract gives each a sentence. What a `Camera` holds is a table in docs/nodes.md
  that nothing checks, so a node that writes `focal` where the next reads `fx` fails inside that
  next node, far from its cause.

## Requirements

1. **A JSON port type may declare its shape** in `contracts/port-types.json`, in the grammar
   `contracts/protocol.json` already uses for its records (fields, each with a type, `of`,
   `optional`, `null`), read and checked by the code that reads those.
2. **A `json` param names a port type that has a shape** (open question 2). Its value in a graph is
   JSON held to that shape when the graph is planned; a wrong one is a problem of that node and
   param which names the place in the value (`clicks.points[2].x`).
3. **A value is held to its type's shape where it is made:** a node whose output of a type with a
   shape does not fit it fails with kind `contract`, naming the field.
4. **A `json` param is in the key by its content,** whatever the order of its keys; and a type's
   shape is in the key of the nodes that name the type, so no value made before a shape changed is
   served unchecked.
5. **Two types get a shape now** (open question 1): `Clicks`, which step 4 needs, and `Camera`, as
   docs/nodes.md documents it and the three model nodes write it.
6. **Clicks reach a graph as a value on a port** (open question 3): a built-in node,
   `source.clicks`, gives the `Clicks` its param holds, so a node takes clicks from a person or
   from another node alike.
7. **The page draws a `json` param** as text the person edits as JSON, read back as an object, a
   syntax error shown as they type: the control every structured type has from the start, usable
   from the keyboard. A value the shape refuses is shown on its card when Run is pressed, as any
   param's problem is today. An editor of its own for a type (clicks placed on the photo) comes
   with the page work that needs it.

## Acceptance criteria

- [ ] AC1: `test_a_json_param_is_held_to_its_types_shape` (`test_graph.py`): for a test node with a
  `json` param, `plan()` accepts a value that fits its type's shape, and refuses one with a wrong
  item in a list with a problem that names the node, the param and the place.
- [ ] AC2: `test_a_json_param_needs_a_type_with_a_shape` (`test_ports_and_manifests.py`): a manifest
  whose `json` param names a type with no shape is refused, naming the param and the type.
- [ ] AC3: `test_a_json_value_is_held_to_its_shape_where_it_is_made` (`test_scheduler.py`): a test
  node that writes a `Camera` without `fx` fails with kind `contract`, the message naming `fx`.
- [ ] AC4: `test_a_closed_shape_refuses_a_field_it_does_not_declare` (`test_ports_and_manifests.py`):
  a `Clicks` with one more field is refused, and a `Camera` with one more passes.
- [ ] AC5: `test_a_json_param_is_in_the_key_by_its_content` (`test_cache_keys.py`): two values that
  differ only in the order of their keys give one key; a changed number gives another.
- [ ] AC6: `test_source_clicks_gives_the_clicks_it_was_given` (`test_server_and_nodes.py`): a graph
  of `source.clicks` alone, run through the server, ends done with a `Clicks` value whose file
  equals the param as parsed JSON.
- [ ] AC7: "a json param is edited as text and read back as an object" (`tests/app/page.test.js`):
  the form gives a `json` param the control `json`; text that is not JSON is a problem, and text
  that is reads back as the object, which the graph model then holds.
- [ ] AC8: CI's `models (cpu)` job passes on Windows and Ubuntu: the `Camera` each of the three
  model nodes writes from its real weights fits the shape (the job's run on this spec's PR).

## Design

**Shapes.** A type in `contracts/port-types.json` whose carrier is `json` may hold `shape`: a
record in protocol.json's grammar (`{fields, open}`), which may name shared sub-records the file
declares under `shapes` (a point, a box). `oneframe/protocol.py`'s reader of a record and its
checker of a value, which are pure and take a list of problems, move to a module of their own,
`oneframe/records.py`, standard library only, which `protocol.py` and `ports.py` both use: one
grammar, one checker. `PortType` gains `shape`, and `ports.list` answers it. The shape is part of
the type's row, so 019's key holds it. The grammar has no fixed length, so a pose is "rows of
numbers" in the shape and 4 by 4 in its `doc`.

**The two shapes** (open question 1):

| Type | Fields |
| --- | --- |
| `Clicks` | `width`, `height` (integers: the image they were placed on, in pixels); `points`: a list of `{x, y, positive}` (numbers, a boolean); `boxes`: a list of `{x0, y0, x1, y1}`; closed |
| `Camera` | `fx`, `fy`, `cx`, `cy` (numbers), `width`, `height` (integers); `pose` optional, rows of numbers; open, so a node may add fields |

**The param** (`manifest.py`, `graph.py`). `json` joins the param types in
`contracts/vocabulary.json`. A manifest writes `{"type": "json", "of": "Clicks"}` (open question
2); `Param` gains `of`, and `_param` refuses a missing or unknown type, one that is not JSON or has
no shape, and a default that does not fit. `check_param` checks a value against the shape, and so
holds a graph's value, a default and `nodes.fit`'s params to one rule. A `json` param always
affects output, and cannot be a factor of a memory model or set by one of its changes. The node
reads it parsed, in `ctx.params`. `Cache.key` already writes params in canonical JSON; AC5 holds it.

**Where a value is made.** The check of what a node made (`scheduler.py`'s `_check_outputs`) moves
to its own module, `oneframe/outputs.py`, as the review asked before it grows, and gains this: for
an output whose type has a shape, the engine reads the file and checks it, after the facets. These
files are small JSON, and the engine loads nothing new.

**`nodes/source.clicks`** (new, an engine node, category `source`): one param, `clicks` (`json` of
`Clicks`, default: no points and no boxes on an image of no size), one output, `clicks`. It writes
the param as its file, and states no trust.

**The page** (`app/renderer/form.js`, `main.js`): `CONTROLS` gains `json`, drawn as a labelled text
area holding the value as indented JSON. `readControl` parses it and answers the object or the
problem at once; what the shape refuses comes back from `graph.run`'s refusal, on the card.

**Docs:** nodes.md (the `json` param, shapes, the "File conventions" table's `Camera` row now the
contract's reading), architecture.md (port types' row), `app/protocol.d.ts` generated again.

Risks: a model node writes a `Camera` the shape refuses (AC8 is the check, and a cloud session
cannot run it: the nodes' code is read against the shape before the PR, and CI decides); a shape
too strict for a later node (it grows by optional fields without breaking a producer).

## Tests

- Added: the seven tests the criteria name; the cases of AC2's test for the other refusals (no
  type, an unknown one, one that is not JSON, a default that does not fit); a test in
  `test_rules.py` that `protocol.py` and `ports.py` define no record reader of their own.
- Changed: `test_depth_to_points_unprojects_through_the_pinhole` (`test_server_and_nodes.py`): its
  test node's `Camera` gains `width` and `height`, which the shape requires.
- Removed: none
- Renamed: none

## Verification

AC1 to AC7 are tests in `npm run check` or `npm run engine:check`, in CI on Windows and Ubuntu.
AC8 is CI's `models (cpu)` job on the PR, which runs the real weights; a cloud session cannot run
it, and the PR says so until the job is green. No hardware step.

## Decisions taken

- Shapes use protocol.json's grammar, not JSON Schema: the engine has its reader already, and a
  library for the other would be a new dependency in the light engine.
- A value on a port is checked too, not only a param: otherwise clicks from a person are held to
  the shape and clicks from a node are not.
- `Text` gets no shape yet: about 35 test nodes write a `Text` as they please, and no shipped node
  reads one, so its shape would be a guess paid for across the suite.
- `Camera`'s shape is open and `Clicks`'s closed: nodes already add camera fields of their own;
  nothing writes clicks yet, so its fields can be exact.
- `source.clicks` states no trust: a click is neither seen in the photo nor a model's guess, and
  what is made from it takes its trust from the photo and the model.
- A text area for every `json` param, before any editor of its own: it is the keyboard's way in,
  which the page's rule asks of every control.

## Out of scope

- An editor that places clicks on the photo: step 4's page work, with SAM 2.1.
- Shapes for `Pose`, `CameraPath`, `Scene`, `Text` and `Metric`: each with its first node.
- Other authoring nodes (a prompt, a camera): each with the node that reads its type.

## Open questions (answered by the owner, 2026-11-12: every one as recommended)

1. **The two shapes** in the table above. A shape is a format every node of the type reads and
   writes. `Clicks` holds the size of the image the clicks were placed on, so a node can refuse
   clicks placed on another image; `positive` is a boolean, not a label number. `Camera` is what
   nodes.md documents today, `width` and `height` required. Recommended: as the table.
2. **How a manifest writes the param:** `{"type": "json", "of": "Clicks"}`, a format node authors
   write. Recommended: yes.
3. **Clicks as a value on a port,** through `source.clicks`, or as a param of the node that uses
   them. A port lets another node give the clicks (a detector's boxes) and shows in the graph where
   they come from, at the cost of a second node in every graph that uses clicks. Recommended: a
   port; a node may still declare a `json` param of its own.
