# 020: Data a person authors: structured params

Status: draft
Owner approval: (date, once approved)

The second of four specs for step 3c, graph model 2: [019](../019-contracts/spec.md) (the
contracts), this one, [021](../021-collections/spec.md) (collections and "for each") and
[022](../022-runs/spec.md) (runs). From the [review of 2026-11-04](../../docs/reviews/2026-11-04.md),
section 1, item 4. It lands after 019, whose vocabulary contract holds the param types.

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
2. **A `json` param names a port type that has a shape** (`"of": "Clicks"`). Its value in a graph is
   JSON held to that shape when the graph is planned; a wrong one is a problem of that node and
   param which names the place in the value (`clicks.points[2].x should be a number`).
3. **A value is held to its type's shape where it is made:** a node whose output of a type with a
   shape does not fit it fails with kind `contract`, naming the field.
4. **A `json` param is in the key by its content,** whatever the order of its keys.
5. **Three types get a shape now** (open question 1): `Clicks`, which step 4 needs; `Camera`, as
   docs/nodes.md documents it and the three model nodes write it; and `Text`.
6. **Clicks reach a graph as a value on a port:** a built-in node, `source.clicks`, gives the
   `Clicks` its param holds, so a node takes clicks from a person or from another node alike.
7. **The page draws a `json` param** as text the person edits as JSON, read back as an object, a
   syntax error shown at once: the control every structured type has from the start, usable from
   the keyboard. An editor of its own for a type (clicks placed on the photo) comes with the page
   work that needs it.

## Acceptance criteria

- [ ] AC1: `test_a_json_param_is_held_to_its_types_shape` (`test_graph.py`): for a test node with a
  `json` param of a type with a shape, `plan()` accepts a value that fits and refuses three that do
  not (a missing field, a wrong type, a wrong item in a list), each problem naming the place.
- [ ] AC2: `test_a_json_param_needs_a_type_with_a_shape` (`test_ports_and_manifests.py`): a manifest
  whose `json` param names no type, an unknown type, a type that is not JSON, or one with no shape
  is refused, each named, as is a default that does not fit.
- [ ] AC3: `test_a_json_value_is_held_to_its_shape_where_it_is_made` (`test_scheduler.py`): a test
  node that writes a `Camera` without `fx` fails with kind `contract`, the message naming `fx`; one
  that writes a field beside the declared ones passes where the shape is open.
- [ ] AC4: `test_a_json_param_is_in_the_key_by_its_content` (`test_cache_keys.py`): two values that
  differ only in the order of their keys give one key; a changed number gives another.
- [ ] AC5: `test_source_clicks_gives_the_clicks_it_was_given` (`test_server_and_nodes.py`): a graph
  of `source.clicks` alone, run through the server, ends done with a `Clicks` value whose file
  equals the param as parsed JSON.
- [ ] AC6: "a json param is edited as text and read back as an object" (`tests/app/page.test.js`):
  the form gives a `json` param the control `json`; text that is not JSON is a problem, and text
  that is reads back as the object, which the graph model then holds.
- [ ] AC7: CI's `models (cpu)` job passes on Windows and Ubuntu: the `Camera` each of the three
  model nodes writes from its real weights fits the shape (the job's run on this spec's PR).

## Design

**Shapes.** A type in `contracts/port-types.json` whose carrier is `json` may hold `shape`: a
record in protocol.json's grammar (`{fields, open}`), which may name shared sub-records the file
declares under `shapes`. `oneframe/protocol.py`'s reader and checker of a record move to a module
of their own, `oneframe/records.py`, standard library only, which `protocol.py` and `ports.py` both
use: one grammar, one checker. `PortType` gains `shape`; `ports.list` answers it, so the page
receives it as it receives facets. A problem's place is written as a path into the value.

**The three shapes** (open question 1), each as its type's `shape`:

| Type | Fields |
| --- | --- |
| `Clicks` | `width`, `height` (integers: the image they were placed on, in pixels); `points`: a list of `{x, y, positive}` (numbers, a boolean); `boxes`: a list of `{x0, y0, x1, y1}` |
| `Camera` | `fx`, `fy`, `cx`, `cy` (numbers), `width`, `height` (integers); `pose` optional, 4 rows of 4 numbers; open, so a node may add fields |
| `Text` | `text` (a string) |

**The param** (`manifest.py`, `graph.py`). `json` joins the param types in
`contracts/vocabulary.json` (019). `Param` gains `of`; `_param` refuses the cases of AC2;
`check_param` checks a value against the shape, and so holds a graph's value, a default and
`nodes.fit`'s params to one rule. A `json` param always affects output, and cannot be a factor of a
memory model or set by one of its changes, which the manifest check says. The node reads it parsed,
in `ctx.params`. The key needs no change: `Cache.key` already writes params in canonical JSON; AC4
holds it.

**Where a value is made** (`scheduler.py`, `_check_outputs`): for an output whose type has a shape,
the engine reads the file and checks it, after the facets. These files are small JSON; the engine
stays light, as it loads nothing new. With 021, each item of a collection is checked the same way.

**`nodes/source.clicks`** (new, an engine node, category `source`): one param, `clicks`
(`json` of `Clicks`, default: no points and no boxes on an image of no size), one output, `clicks`,
trust `measured`, since a person placed them. It writes the param as its file.

**The page** (`app/renderer/form.js`, `main.js`): `CONTROLS` gains `json`, drawn as a labelled text
area holding the value as indented JSON. `readControl` parses it and answers the object or the
problem; the engine's answer to `graph.validate` shows the shape's problems on the card, as it
shows any param's today. 019's AC4 holds that the control exists.

**Docs:** nodes.md (the `json` param, shapes, the "File conventions" table's `Camera` row now the
contract's reading), architecture.md (port types' row), `app/protocol.d.ts` generated again.

Risks: a model node writes a `Camera` the shape refuses (AC7 is the check, and a cloud session
cannot run it: the nodes' code is read against the shape before the PR, and CI decides); a shape
too strict for a later node (it is open where nodes may add, and a shape grows by optional fields
without breaking a producer).

## Tests

- Added: the six tests the criteria name; `test_records_are_read_and_checked_once`
  (`test_protocol.py`): the protocol's and the port types' records go through `records.py`.
- Changed: tests that import the record reader from `protocol.py` import it from `records.py`.
- Removed: none
- Renamed: none

## Verification

AC1 to AC6 are tests in `npm run check` or `npm run engine:check`, in CI on Windows and Ubuntu.
AC7 is CI's `models (cpu)` job on the PR, which runs the real weights; a cloud session cannot run
it, and the PR says so until the job is green. No hardware step.

## Decisions taken

- Shapes use protocol.json's grammar, not JSON Schema: the engine has its reader already, and a
  library for the other would be a new dependency in the light engine.
- A value on a port is checked too, not only a param: otherwise clicks from a person are held to
  the shape and clicks from a node are not.
- Clicks are a value on a port through a source node, not a param of the node that uses them: one
  input then takes them from a person or from another node, and the graph shows where they come
  from.
- `Camera`'s shape is open and `Clicks`'s closed: nodes already add camera fields of their own;
  nothing writes clicks yet, so its fields can be exact.
- A text area for every `json` param, before any editor of its own: it is the keyboard's way in,
  which the page's rule asks of every control.

## Out of scope

- An editor that places clicks on the photo: step 4's page work, with SAM 2.1.
- Shapes for `Pose`, `CameraPath`, `Scene` and `Metric`: each with its first node, since a shape
  nothing writes is a guess.
- `source.text` and other authoring nodes: each with the node that reads its type.

## Open questions

1. **The three shapes** in the table above. A shape is a format every node of the type reads and
   writes. `Clicks` holds the size of the image the clicks were placed on, so a node can refuse
   clicks placed on another image; `positive` is a boolean, not a label number. `Camera` is what
   nodes.md documents today. Recommended: as the table.
