# 019: Tasks

Each task leaves `npm run check` and `npm run engine:check` green. Tick as they land.

- [x] 1. **The vocabulary contract.** `contracts/vocabulary.json` (categories with their docs, `sound`
  among them and `test` not; the param types); `oneframe/vocabulary.py` reads it; `manifest.py`
  keeps no list of its own; every test manifest of category `test` takes `evaluate`. AC1, AC2, AC3.
- [x] 2. **Devices named once.** `oneframe/node/devices.py`; every other module imports its names;
  `manifest.py` loses `DEVICES`; `test_devices_are_named_once`, with the two places where the
  letters mean something else. AC4.
- [x] 3. **The port contract in the key.** `ports.contract(names)`; `Cache.key` holds the rows of
  the types a manifest's ports name; `KEY_VERSION` 3; the recorded cache key's new value. AC5.
- [x] 4. **Scene facets and `Audio`.** `contracts/port-types.json` as the spec's table, `Audio` with
  the `wav` carrier; nodes.md's file table. AC6, AC7.
- [x] 5. **A value names its sources.** `meta.sources` written in `_keep`; the port contract's
  comment says so. AC8.
- [ ] 6. **Docs and the end.** architecture.md, nodes.md, AGENTS.md's "Nothing hard-coded" (open
  question 4), the roadmap, the handoff; the full checks; `/code-review`; the spec marked done in
  the PR.
