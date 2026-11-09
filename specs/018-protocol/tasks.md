# 018: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **The contract and its reader.** `contracts/protocol.json` (`"format": 1`): `methods`,
  `events` and `shapes`, declaring what the code sends today, read from the code, not from
  architecture.md's prose. `oneframe/protocol.py`: `event`, `check_answer`, `problems`, read
  once with `reading.read_object`, loading no library. AC1.
- [x] 2. **Every event and answer checked.** Each module in the engine's process builds its events
  with `protocol.event` (about 44 names in 52 literals, and the `event=` keyword uses); the
  scheduler checks the child's `stage`, `progress` and `ceiling` as it passes them on;
  `Engine.handle` checks each answer, and `Requests` the `{"stopped": true}` it gives a queued
  request a stop cancels (spec 016), which every method that can be queued declares it may
  answer. `conftest.py`'s autouse check; the suite then names every field the first task missed.
  AC2.
- [x] 3. **The docs say the same.** architecture.md's Events section ends with the list of every
  event, held to the contract both ways. AC3.
- [x] 4. **The page's types.** `scripts/protocol-types.js [--write]` writes `app/protocol.d.ts`, run
  by `npm run check` without `--write`; the preload, `door.js`, `engine.js` and the page import
  its types by JSDoc. AC4.
- [x] 5. **The door strips by the contract.** `forPage` removes the fields marked `"page": false`
  instead of its `PATH_FIELDS`, keeps its scrubbing of the root, the repo and the home folder, and
  logs a message the contract does not declare, once per name. AC5.
- [x] 6. **Every method declared.** `engine.test.js`: each of `ENGINE_METHODS`, `APP_METHODS` and
  the engine's own table (its hello's `methods`), with its params and its answer. AC6.
- [ ] 7. **Docs and the end.** architecture.md (the contract is the declaration), nodes.md (a node's
  `stage` and `progress` fields), the commands list (`protocol-types.js`), `/code-review`, and the
  spec marked done in the PR.
