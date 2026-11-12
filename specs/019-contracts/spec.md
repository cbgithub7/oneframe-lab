# 019: The contracts, grown before nodes depend on them

Status: draft
Owner approval: (date, once approved)

The first of four specs for step 3c, graph model 2, which is too large for one: this one,
[020](../020-structured-params/spec.md) (data a person authors), [021](../021-collections/spec.md)
(collections and "for each") and [022](../022-runs/spec.md) (runs up to a node, several at once).
From the [review of 2026-11-04](../../docs/reviews/2026-11-04.md), section 1, item 5, and section
2.2. It comes first because it changes every cache key, once, and the other three build on it.

## Problem

- **The vocabularies live in engine code.** Categories, param types and devices are tuples in
  `oneframe/manifest.py`. The page copies the param types by hand (`app/renderer/form.js`), a sound
  node needs an engine edit for its category, `"test"` is in the shipped list, and `"cuda"` and
  `"cpu"` are spelled in 15 engine modules.
- **The cache key leaves out the port contract.** After a change to `contracts/port-types.json`,
  entries made under the old contract are still served, and their values then fail as `edge`.
- **A facet added later breaks every producer.** Every value must state every facet of its type
  (`_check_outputs`). Step 4 brings the first nodes that make a `Mesh`, a `GaussianSplat` and a
  `Pose`, whose types do not yet say in which frame or at what scale they are; and sound, in scope
  since 2026-10-05, has no type.
- **The contract claims what no code does:** "every value also carries its source image id".

## Requirements

1. **The vocabularies are data.** `contracts/vocabulary.json` holds the categories, the param types
   and the devices. The engine checks manifests against it and keeps no list of its own; a category
   added there needs no engine edit.
2. **`test` is no category that ships.** The test suite's nodes take shipped categories.
3. **Devices are named once.** One engine module names the devices; no other spells one.
4. **The page's form follows the contract:** a control for every param type in it, held by a test.
5. **The port contract is in the key.** A node's key holds the contract of each type its ports
   name (carrier, facets and their values). A change to a type re-runs the nodes that use it and
   no other, and no entry made under an older contract is served.
6. **Scene values say their frame and scale** (open question 1): `Mesh`, `GaussianSplat`, `Pose`,
   `Scene` and `CameraPath` gain the facets in the table below, before any node makes one.
7. **Sound has a type** (open question 2): `Audio`, a WAV file the value protocol serves, and a
   `sound` category.
8. **A value names its sources** (open question 3): the engine writes on every value the values it
   descends from that no step made from another (a photo, and later clicks or a prompt), as it
   writes `made_with`. The contract's comment then says what the code does.

## Acceptance criteria

- [ ] AC1: `test_manifests_are_checked_against_the_vocabulary_contract`
  (`test_ports_and_manifests.py`): against a copy of the contract with one more category, a
  manifest of that category is accepted; against the shipped one it is refused, naming the shipped
  categories. Fails today: the list is a tuple in `manifest.py`.
- [ ] AC2: `test_test_is_no_shipped_category` (`test_ports_and_manifests.py`): a manifest of
  category `test` is refused against the shipped contract. Fails today.
- [ ] AC3: `test_devices_are_named_once` (`test_rules.py`): over the syntax trees of every module
  in `engine/src/oneframe`, a string constant equal to a device of the contract appears in one
  module only, and that module's names equal the contract's devices. Fails today: 15 modules.
- [ ] AC4: "the form has a control for every param type in the contract" (`tests/app/page.test.js`):
  each param type in `contracts/vocabulary.json` gives a control other than `unknown`.
- [ ] AC5: `test_a_types_contract_is_in_the_key_of_the_nodes_that_use_it` (`test_cache_keys.py`):
  against a copy of the port contract where one type gains a facet value, the key of a node whose
  ports name that type changes and the key of a node that names none of it stays. Fails today.
- [ ] AC6: `test_scene_values_say_their_frame_and_scale` (`test_scheduler.py`): a test node whose
  `Mesh` output leaves out `frame` fails with kind `contract`, and `plan()` refuses an edge from a
  `Mesh[frame=object]` output to a `Mesh[frame=camera|world]` input. Fails today: no such facet.
- [ ] AC7: `test_audio_is_a_type_the_value_protocol_serves` (`test_server_and_nodes.py`): a test
  node with an `Audio` output runs, and `values.locate` answers its file with the carrier `wav`,
  which the contract gives the media type `audio/wav`. Fails today: no such type.
- [ ] AC8: `test_a_value_names_the_sources_it_descends_from` (`test_scheduler.py`): with two source
  steps, the output of a step fed by one names that one in `meta.sources`, and the output of a
  step fed by both names both, each with its type. Fails today: nothing is written.

## Design

**`contracts/vocabulary.json`** (new, with a `format`): `categories` (each with a `doc`, the list
`manifest.py` holds today without `test`, and `sound`), `param_types` and `devices`.
`oneframe/vocabulary.py` reads it once, as `ports.types()` reads its file, and takes a path so a
test can give a copy. `manifest.py` loses `CATEGORIES`, `PARAM_TYPES` and `DEVICES`.

**Devices.** The names live in `oneframe/node/devices.py` (`CPU`, `CUDA`), since node code runs
where nothing else of the engine is importable and `oneframe.node` may import nothing outside
itself; the engine imports them from there. A test holds them equal to the contract's `devices`.
Composed names (`cuda:8.6` in a memory model's `time`) are built from them.

**The key.** `ports.contract(names)` gives, for the types named, their carriers and facets as plain
data; `Cache.key` puts it in the body it already hashes, for the types of the manifest's ports, so
no new hash is written, and `KEY_VERSION` becomes 3. Every entry made before is orphaned once;
nothing evicts them until the cache gets its cap (step 6).

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
camera frame is the OpenCV frame with y and z negated. No shipped node or graph names these types,
so nothing migrates.

**`Audio`** (open question 2): carrier `wav`, served as `audio/wav`; facets `channels` (mono,
stereo) and `loop` (seamless, none); its `doc` says PCM. docs/nodes.md gains its row.

**Sources** (`scheduler.py`, in `_keep`, where `made_with` is written): a step with no connected
input starts a source, and each of its outputs lists itself; every other output lists the union of
its inputs' lists, as `meta.sources`, rows of `{id, type}` sorted by id. A node cannot set it. It
is written to the entry, so a cached value has it. The port contract's comment is reworded to this.

**Docs:** architecture.md (the parts table, the cache's sentence), nodes.md (categories and devices
from the contract, the new facets, `Audio`), AGENTS.md's "Nothing hard-coded", which gains the
vocabulary beside the port types, and the roadmap's sound line.

Risks: a device spelled in a new module (AC3 fails it); a model node's results orphaned by the
key (they are re-run once; CI's `models (cpu)` job starts from an empty cache anyway).

## Tests

- Added: the eight tests the criteria name; a test that the node package's device names equal the
  contract's.
- Changed: every test node of category `test` takes a shipped category (never `convert`, which the
  plan reads).
- Removed: none
- Renamed: none

## Verification

Every criterion is a test in `npm run check` or `npm run engine:check`, in CI on Windows and Ubuntu.
CI's `models (cpu)` job must still pass: it runs the three model nodes under the new key. No
hardware step.

## Decisions taken

- The contract of the types a node names is in its key, not the whole file's digest: adding a type
  then re-runs nothing.
- The page reads the vocabulary in a test, not over the protocol: it needs the param types only to
  be held to them, and shows a category by its id as it does now.
- Devices are constants in the node package and a list in the contract, held equal by a test: code
  that branches on a device needs a name, and node code can import only that package.
- Test nodes take shipped categories rather than a test-only contract: one contract is read.
- `meta.sources` rather than a field of `Value`: the node API does not change, as for `made_with`.

## Out of scope

- A player for `Audio` in the page, and any node that makes sound or a scene type: step 4 and the
  roadmap.
- Titles for categories in the page: the editor, step 7.
- Evicting the entries a new key orphans: step 6's cache cap.
- Shapes for the JSON types (`Pose`, `Clicks`): 020.

## Open questions

1. **The scene facets** in the table above. A type's facets are a format every node of that type
   writes, hard to change once nodes do. Recommended: as the table, `pose` replaced by `frame`.
2. **`Audio`:** WAV (lossless, read by Python's standard library and by the page's own player),
   with `channels` and `loop`. Recommended: yes; a compressed carrier can be a later export node.
3. **Sources carried, or the claim removed.** Carrying costs a few lines now, while every key
   changes anyway, and lets a node that aligns two values (a mask and a depth map) refuse ones
   from different photos. Removing the claim costs nothing and leaves that for the spec that needs
   it. Recommended: carry them.
