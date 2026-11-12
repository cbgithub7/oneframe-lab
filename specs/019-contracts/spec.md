# 019: The contracts, grown before nodes depend on them

Status: draft
Owner approval: (date, once approved)

The first of five specs for step 3c, graph model 2, which is too large for one: this one,
[020](../020-structured-params/spec.md) (data a person authors), [021](../021-collections/spec.md)
(collections and "for each"), [022](../022-run-to-a-node/spec.md) (a run up to a node) and
[023](../023-runs-at-once/spec.md) (several runs at once). From the
[review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 1, item 5, and section 2.2. It
comes first because it changes every cache key, and the others build on it.

## Problem

- **The vocabularies live in engine code.** Categories and param types are tuples in
  `oneframe/manifest.py`. The page copies the param types by hand (`app/renderer/form.js`), a sound
  node needs an engine edit for its category, and `"test"` is in the shipped list. `"cuda"` and
  `"cpu"` are spelled as devices in 13 engine modules.
- **The cache key leaves out the port contract.** After a change to `contracts/port-types.json`,
  entries made under the old contract are still served, and their values then fail as `edge`.
- **A facet added later breaks every producer.** Every value must state every facet of its type
  (`_check_outputs`). Step 4 brings the first nodes that make a `Mesh`, a `GaussianSplat` and a
  `Pose`, whose types do not yet say in which frame or at what scale they are; and sound, in scope
  since 2026-10-05, has no type.
- **The contract claims what no code does:** "every value also carries its source image id".

## Requirements

1. **Categories and param types are data.** `contracts/vocabulary.json` holds them. The engine
   checks manifests against it and keeps no list of its own; a category added there needs no
   engine edit. `test` is no category that ships: the test suite's nodes take shipped ones.
2. **Every param type in the contract is checked and drawn:** the engine refuses a wrong value of
   each, and the page's form has a control for each, both held by tests.
3. **Devices are named once.** One engine module names the devices; no other spells one as a device.
4. **The port contract is in the key.** A node's key holds everything the contract says of each
   type its ports name, but its `doc`. A change to a type re-runs the nodes that use it and what
   follows them, and no entry made under an older contract is served.
5. **Scene values say their frame and scale** (open question 1): `Mesh`, `GaussianSplat`, `Pose`,
   `Scene` and `CameraPath` gain the facets in the table below, before any node makes one.
6. **Sound has a type** (open question 2): `Audio`, a WAV file the value protocol serves, and a
   `sound` category.
7. **A value names its sources** (open question 3): the engine writes on every value the values it
   descends from that no step made from another (a photo, and later clicks or a prompt), as it
   writes `made_with`. The contract's comment then says what the code does.

## Acceptance criteria

- [ ] AC1: `test_manifests_are_checked_against_the_vocabulary_contract`
  (`test_ports_and_manifests.py`): against a copy of the contract with one more category, a
  manifest of that category is accepted; against the shipped one, a manifest of category `test` is
  refused, naming the shipped categories. Fails today: the list is a tuple, with `test` in it.
- [ ] AC2: `test_every_param_type_in_the_contract_is_checked` (`test_ports_and_manifests.py`): for
  each param type in the contract, `check_param` refuses a value of the wrong kind.
- [ ] AC3: "the form has a control for every param type in the contract" (`tests/app/page.test.js`):
  each param type in `contracts/vocabulary.json` gives a control other than `unknown`.
- [ ] AC4: `test_devices_are_named_once` (`test_rules.py`): over the syntax trees of every module
  in `engine/src/oneframe`, a string constant that spells a device stands in one module only, but
  for the places the test lists by name, where the same letters mean something else. Fails today:
  13 modules.
- [ ] AC5: `test_a_types_contract_is_in_the_key_of_the_nodes_that_use_it` (`test_cache_keys.py`):
  against a copy of the port contract where one type gains a facet value, the key of a node whose
  ports name that type changes and the key of a node that names none of it stays. Fails today.
- [ ] AC6: `test_scene_types_say_frame_and_scale` (`test_ports_and_manifests.py`): each of the five
  types has `frame` and `scale` with the table's values, and none has `pose`. Fails today.
- [ ] AC7: `test_audio_is_a_type_the_value_protocol_can_serve` (`test_server_and_nodes.py`): a test
  node with an `Audio` output runs, and `values.locate` answers its file with the carrier `wav`.
  Fails today: no such type. (`tests/app/values.test.js` already holds every carrier to a media
  type, so the new row must give one.)
- [ ] AC8: `test_a_value_names_the_sources_it_descends_from` (`test_scheduler.py`): with two source
  steps, the output of a step fed by one names that one in `meta.sources`, and the output of a
  step fed by both names both, each by its value's key and with its type. Fails today.

## Design

**`contracts/vocabulary.json`** (new, with a `format`): `categories` (each with a `doc`: the list
`manifest.py` holds today without `test`, and `sound`) and `param_types`. `convert`'s `doc` says it
is the one category the engine reads (`registry.converters`). `oneframe/vocabulary.py` reads the
file once, as `ports.types()` reads its own, and takes a path so a test can give a copy.
`manifest.py` loses `CATEGORIES` and `PARAM_TYPES`. A param type still needs its check in
`check_param` and its control in the form; AC2 and AC3 fail a type that has neither.

**Devices** stay code, since the fit and the executors branch on them: `oneframe/node/devices.py`
(`CPU`, `CUDA`, `DEVICES`), in the node package because node code can import nothing else of the
engine, and the engine imports them from there. `manifest.py` loses `DEVICES`. Two places keep the
letters with another meaning, and AC4's test lists them: a cache folder's name in `environment.py`,
and the `cpu` field of a memory model's precision row in `memory/model.py`.

**The key.** `ports.contract(names)` gives, for the types named, their rows without `doc`, as plain
data; `Cache.key` puts it in the body it already hashes, for the types of the manifest's ports, so
no new hash is written, and `KEY_VERSION` becomes 3. A later change to a type (020's shapes) then
re-runs that type's nodes by this mechanism. Every entry made before is orphaned once; nothing
evicts them until the cache gets its cap (step 6).

**Scene facets** (`contracts/port-types.json`, open question 1):

| Type | Facets after | Change |
| --- | --- | --- |
| `Mesh` | `frame`: object, camera, world; `scale`: metric, up_to_scale; `appearance` as now | `pose` (canonical, posed) becomes `frame` |
| `GaussianSplat` | `frame`: object, camera, world; `scale`: metric, up_to_scale | `pose` becomes `frame` |
| `Pose` | `frame`: camera, world (the frame it places into); `scale`: metric, up_to_scale | new |
| `Scene`, `CameraPath` | `frame`: camera, world; `scale`: metric, up_to_scale | new |

The words are `PointMap`'s, so one vocabulary covers points, assets and scenes. `object` is an
asset's own frame, as a generator gives it. Each type's `doc` states its axes: a splat's are
OpenCV's, as a point map's; a GLB's are glTF's (x right, y up, z toward the viewer), which in the
camera frame is the OpenCV frame with y and z negated. No shipped node, graph or test names these
types, so nothing migrates.

**`Audio`** (open question 2): carrier `wav`, served as `audio/wav`; facets `channels` (mono,
stereo) and `loop` (seamless, none); its `doc` says PCM. docs/nodes.md gains its row.

**Sources** (`scheduler.py`, in `_keep`, where `made_with` is written): a step with no connected
input starts a source, and each of its outputs lists itself; every other output lists the union of
its inputs' lists, as `meta.sources`: rows of `{id, type}`, `id` the value's key
(`<entry>:<port>`, the same in every graph that shares the entry), sorted by it. A node cannot set
it. It is written to the entry, so a cached value has it. The fit reads only numbers in an input's
meta, so nothing a machine learned moves. The port contract's comment is reworded to this.

**Docs:** architecture.md (the parts table, the cache's sentence), nodes.md (categories from the
contract, the new facets, `Audio`), the roadmap's sound line, and AGENTS.md (open question 4).

Risks: a device spelled in a new module (AC4 fails it); a model node's results orphaned by the key
(they are re-run once; CI's `models (cpu)` job starts from an empty cache anyway).

## Tests

- Added: the eight tests the criteria name.
- Changed: the 56 test manifests of category `test`, in 22 files, take a shipped category (never
  `convert`, which the plan reads): the engine's tests, the hardware check's node
  (`engine/tests/hardware/test.vram`) and `tests/app/door.test.js`.
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run check` or `npm run engine:check`, in CI on Windows and Ubuntu.
CI's `models (cpu)` job must still pass: it runs the three model nodes under the new key. No
hardware step.

## Decisions taken

- A type's own row is in the key of the nodes that name it, not the whole file's digest: adding a
  type then re-runs nothing.
- Devices are not in the contract: code branches on them, so a device added as data alone would
  be accepted and never run. The review asked for them "named once".
- The page reads the vocabulary in a test, not over the protocol: it needs the param types only to
  be held to them, and shows a category by its id as it does now.
- Test nodes take shipped categories rather than a test-only contract: one contract is read.
- `meta.sources` rather than a field of `Value`: the node API does not change, as for `made_with`.

## Out of scope

- A player for `Audio` in the page, and any node that makes sound or a scene type: step 4 and the
  roadmap.
- Titles for categories in the page: the editor, step 7.
- Evicting the entries a new key orphans: step 6's cache cap.
- Shapes for the JSON types (`Camera`, `Clicks`): 020.

## Open questions (answered by the owner, 2026-11-12: every one as recommended)

1. **The scene facets** in the table above. A type's facets are a format every node of that type
   writes, hard to change once nodes do. Recommended: as the table, `pose` replaced by `frame`.
2. **`Audio`:** WAV (lossless, read by Python's standard library and by the page's own player),
   with `channels` and `loop`. Recommended: yes; a compressed carrier can be a later export node.
3. **Sources carried, or the claim removed.** Carrying costs a few lines now, while every key
   changes anyway, and lets a node that aligns two values (a mask and a depth map) refuse ones
   from different photos. Removing the claim costs nothing and leaves that for the spec that needs
   it. Recommended: carry them.
4. **The rule "Nothing hard-coded"** in AGENTS.md would read: "the engine learns them from
   `nodes/*/node.json`, ports from `contracts/port-types.json`, and categories and param types from
   `contracts/vocabulary.json`", and name `test_devices_are_named_once` beside the test that holds
   it. Recommended: yes.
