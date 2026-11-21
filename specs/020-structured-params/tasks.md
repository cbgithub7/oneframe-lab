# 020: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **One grammar for records.** `oneframe/records.py` holds protocol.py's reader of a record
  and its checker of a value, which `protocol.py` uses; a rules test holds that neither
  `protocol.py` nor `ports.py` defines a reader of its own.
- [ ] 2. **Shapes on JSON port types.** `contracts/port-types.json`'s `shapes` (a point, a box) and
  the `shape` of `Clicks` and `Camera`; `PortType.shape`, `ports.check_shape`; the key's contract
  part holds the shapes a type names; `ports.list` answers the shape (`contracts/protocol.json`,
  `app/protocol.d.ts`). AC4.
- [ ] 3. **The `json` param.** `json` in `contracts/vocabulary.json`; `Param.of`; the manifest's
  refusals; `check_param` against the shape; a memory model cannot read or set one. AC1, AC2, AC5.
- [ ] 4. **What a node made, in its own module.** `oneframe/outputs.py` takes the scheduler's
  output check and holds a JSON output to its type's shape; the test node's `Camera` gains its
  size. AC3.
- [ ] 5. **`source.clicks`.** An engine node that gives the clicks its param holds. AC6.
- [ ] 6. **The page's `json` control.** A text area, read back as an object. AC7.
- [ ] 7. **Docs and the end.** nodes.md, architecture.md; the full checks; `/code-review`; the spec
  marked done in the PR, whose `models (cpu)` job is AC8.
