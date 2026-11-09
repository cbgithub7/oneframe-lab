# 018: The protocol, declared

Status: done ([PR #67](https://github.com/cbgithub7/oneframe-lab/pull/67))
Owner approval: 2026-11-08, with 015, 016 and 017 as one decision; no open questions

The fourth of five specs for step 3, first light: [005](../005-first-models/spec.md) (the model
nodes), [015](../015-graphs/spec.md) (graphs as data), [016](../016-door/spec.md) (photos and
results through the door), this one, and [017](../017-first-page/spec.md) (the page). From the
[review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 2, item 1, which that review's
order puts before 017's run view (its section 5, item 3). It lands after 016, whose door it uses,
and before 017, whose page is typed from it.

## Problem

What the engine says to the page is prose. About 43 event names are built as dict literals in 51
places; no schema or test holds them to architecture.md, and the page's types are `any`. 017's
run view would be written against that prose, and a field renamed in the engine would break the
page with no check failing.

## Requirements

1. **One declaration.** `contracts/protocol.json` declares every method of the engine's protocol
   (its params and the fields of its answer) and every event the engine or the app sends to the
   page (its fields, each with its type and whether it is optional), and marks the fields that
   never reach the page.
2. **Nothing undeclared is sent.** Every event the engine makes, and every answer it gives, is
   checked against the declaration. In the tests a problem fails the test that caused it; in the
   app it is logged once per name, and the message still goes.
3. **The docs say the same.** architecture.md's Events section ends with a list of every event by
   name, held to the declaration both ways, as its failure table is held to `oneframe/errors.py`.
4. **The page's types come from it.** `app/protocol.d.ts` is generated from the contract and
   committed, and `npm run check` fails when it is not current. The preload, the door, the engine
   client and the page type their messages from it, never as `any`.
5. **The door strips by the contract.** 016's `forPage` removes the fields the contract marks as
   never reaching the page, instead of a list of its own.

## Acceptance criteria

- [ ] AC1: `test_an_undeclared_event_is_a_problem` (`test_protocol.py`, new): an event the contract
  does not name, one missing a required field, one with an undeclared field, and one with a field
  of the wrong type each record a problem naming the event and the field; a declared event records
  none. Fails today: no contract.
- [ ] AC2: The engine's suite passes with `conftest.py`'s autouse check, which fails any test after
  which `protocol.problems()` holds a problem: every event and answer the suite makes is declared.
- [ ] AC3: `test_architecture_names_every_declared_event` (`test_protocol.py`): the list of every
  event that closes architecture.md's Events section holds exactly the contract's events, both
  ways; the methods its prose names are not in that list.
- [ ] AC4: The node test "the page's types are the contract's" (`protocol.test.js`, new):
  `node scripts/protocol-types.js` fails when `app/protocol.d.ts` differs from what the contract
  gives (a field changed, an event added), naming the difference, and passes on the committed file.
- [ ] AC5: The node test "what reaches the page is declared" (`door.test.js`): every answer and
  event `forPage` passes on from 016's test engine (a broken manifest, a node that fails, a run, a
  stop) matches its declaration and holds no field the contract marks as never reaching the page.
- [ ] AC6: The node test "every method is declared" (`engine.test.js`): each method of
  `ENGINE_METHODS`, of `APP_METHODS` (016) and of the engine's own table is declared, with its
  params and its answer.

## Design

**`contracts/protocol.json`** (new, `"format": 1`) holds `methods`, `events` and `shapes`. A field
is `{"type": ..., "optional": true, "page": false}`, the last two only where they apply. Types are
few: `string`, `integer`, `number`, `boolean`, `object`, `array` (with `of`), and a shape's name
for a nested value the page reads (a fit, a value, and a failure, whose kinds and reasons
`errors.py` declares). An `object` without a shape is free-form (`meta`, `made_with`). Each event says whether
the engine or the app (`engine.exit`, `engine.failed` from `main.js`) sends it.

**`oneframe/protocol.py`** (new) reads the contract once with `reading.read_object`, and has
`event(name, **fields)`, which builds and checks an event, `check_answer(method, result)`, and
`problems()`. It loads no library (the light-engine test still holds). Every module that builds a
declared event in the engine's process does so with `protocol.event`, not a dict literal. The code
that runs in a node's child (`node/context.py`, `node/usage.py`: `stage`, `progress`, `ceiling`)
keeps its literals, since a runtime's Python does not read the contract; the scheduler checks each
such event as it passes it on. `server.py`'s
`handle` checks each answer. A problem is logged once per event or method name and kept for
`problems()`; `conftest.py` gains an autouse fixture that fails a test after which it is not empty,
then clears it.

**The page's types.** `scripts/protocol-types.js` (new) writes `app/protocol.d.ts` from the
contract with `--write`: a type per event, their union discriminated by `event`, and per method its
params and answer. Without `--write` it compares, and `npm run check` runs it, as it runs the docs
check. `preload.cjs`'s `request` and `onEvent`, `door.js`, `engine.js` and the page import these
types by JSDoc.

**The door.** `forPage` (016) removes the fields marked `"page": false` (a value's `path`, a
manifest's `folder`, the hello's `data`) and logs a message the contract does not declare; its
scrubbing of the root, the repo and the home folder in other strings stays as 016 has it.

**Docs:** architecture.md (Events: the contract is the declaration, the prose its reading, and
the list of every event),
nodes.md (a node's `stage` and `progress` fields), the commands list (`protocol-types.js`).

Risks: the first run of the suite with the check finds every field the prose missed (the first
task declares what the code sends today, then the suite names the rest); a child's events are
checked only when the engine passes them on (that is where they reach the page); a thread that
outlives its test records its problem against the next one (each problem names its event, and so
its sender).

## Tests

- Added: `test_an_undeclared_event_is_a_problem`, `test_architecture_names_every_declared_event`,
  `test_queued_methods_are_those_the_server_queues`, `test_the_contract_says_its_problems_at_once`,
  `test_a_contract_that_cannot_be_read_stops_no_message` (`test_protocol.py`, new); in `protocol.test.js` (new), "the page's types are the contract's"; in
  `door.test.js`, "what reaches the page is declared"; in `engine.test.js`, "every method is
  declared".
- Removed: none
- Renamed: none

## Verification

AC1 to AC3 run in `npm run engine:check`, AC4 to AC6 in `npm run check`, in CI on Windows and
Ubuntu. No hardware step.

## Decisions taken

- Its own spec, not 016's, where the review of 2026-11-04 put it: 016 holds eight criteria, the
  most a spec may, and the contract stands alone.
- One builder, `protocol.event`, instead of checking at the server only: tests that drive the
  scheduler without a server are checked too.
- Logged in the app, never refused: a field the contract misses must not stop a person's run; the
  tests are where it fails.
- Types generated and committed, checked as `collected.txt` is: tsc needs the file, and the page
  has no step that could generate it before tsc runs.
- Exact event lists in tests stay as they are: each is the behaviour its test checks, and a new
  event should be seen there.
- A small type list, not JSON Schema: the page needs names, fields and shapes, and the check stays
  a short module the engine can load without a library.
- What never reaches the app stays undeclared: a runtime child's messages to the engine (`pid`,
  `done`, `error`), the journal's records, and the command-line tools' output (`models:pin`'s
  `pin.*`).
- Beside the spec's types, `null` and a list of types for either: a peak may be unmeasured, and
  `models.download`'s `checkpoints` is a list or `"all"`, as the code sends them today.
- A record's `with` carries a shape's fields, all optional where it says so (a failure's fields at
  the top of `node.failed`, `node.skipped`); `open` lets a manifest keep fields its author added.
- `queued` marks each method a stop or the close may answer `{"stopped": true}`, declared once
  as a shape; a test holds the marks to the server's `AT_ONCE` and `STOPS`.
- Error replies are checked as failures, since the page reads them; params are declared for the
  types only, since each method checks its own and refuses `bad_params`.
- A tuple counts as an array in the engine's check, since json writes it so (`ports.list`'s facets).
- `app/protocol.d.ts` has two namespaces: `Wire`, every field, for the main process; `Page`,
  without the fields marked `"page": false`.
- A second reader of the contract, `app/main/protocol.js`: the door checks and strips in the main
  process, where the engine's is not, as `paths.js` mirrors `layout.py`.
- An engine a test starts as a process fails that test with the problems it logged
  (`engine_process`), as the in-process check does.
- Two server tests that held a method of their own hold `nodes.forget` instead: an undeclared
  method is a problem.
- Where the contract leaves a field free-form (`meta`) or declares nothing, the door drops a field
  that holds a local path, whatever its name: 016's rule, kept where no declaration can say.
- A contract that cannot be read stops no message: the engine keeps why, and the app logs each
  message as undeclared.
- A `{"stopped": true}` is handled where the app reads an answer: `photo.pick` answers null, and
  the page's node list stays as it was.

## Out of scope

- A version handshake between app and engine: they ship together, from one commit.
- The graph file's format (015) and the value files' (port types, `contracts/port-types.json`).

## Open questions

None. The contract is internal to the app and the engine, which ship together, so its format is
easy to change; nothing here is a product, licence, platform or rule decision.
