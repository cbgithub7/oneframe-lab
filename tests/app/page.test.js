// @ts-check
// The page's models (spec 017), checked without a browser: each is a plain module under
// app/renderer/ that uses neither Node nor the DOM, so these tests import it as it is.
import assert from "node:assert/strict";
import { test } from "node:test";

import { formModel, readControl } from "../../app/renderer/form.js";
import { feeds, isSet, openGraph, resetParam, setParam } from "../../app/renderer/graph-model.js";

/** @typedef {import("../../app/protocol.js").Page.Manifest} Manifest */

/** A test manifest with one param of each type the engine knows. @type {Manifest} */
const EVERY_PARAM = {
  id: "test.every_param",
  version: "1",
  title: "Every param",
  category: "convert",
  inputs: { image: "Image" },
  outputs: { points: { type: "PointMap[frame=camera]", trust: "predicted" } },
  params: {
    count: { type: "int", default: 4, min: 1, max: 9, label: "Count" },
    scale: { type: "float", default: 0.5, min: 0, max: 2 },
    flip: { type: "bool", default: false },
    mode: { type: "choice", choices: ["fast", "fine"], default: "fine" },
    note: { type: "string", default: "" },
    photo: { type: "file", label: "Photo" },
  },
};

test("a settings form is drawn from a manifest", () => {
  let graph = openGraph({ nodes: { step: { node: EVERY_PARAM.id } }, edges: [{ from: "a.image", to: "step.image" }] });
  const controls = formModel(EVERY_PARAM, graph, "step");
  assert.deepEqual(controls.map((c) => [c.name, c.type, c.control]), [
    ["count", "int", "number"],
    ["scale", "float", "number"],
    ["flip", "bool", "checkbox"],
    ["mode", "choice", "select"],
    ["note", "string", "text"],
    ["photo", "file", "photo"],
  ]);
  const by = Object.fromEntries(controls.map((c) => [c.name, c]));
  assert.deepEqual([by.count.min, by.count.max, by.count.step, by.count.default], [1, 9, 1, 4]);
  assert.deepEqual([by.scale.min, by.scale.max, by.scale.step, by.scale.default], [0, 2, "any", 0.5]);
  assert.deepEqual(by.mode.choices, ["fast", "fine"]);
  assert.equal(by.mode.default, "fine");
  assert.equal(by.count.label, "Count");
  assert.equal(by.note.label, "note"); // a param without a label is named by its key
  assert.ok(controls.every((c) => !c.set), "nothing is set until the person sets it");
  assert.equal(by.count.value, 4, "an unset param shows its default");
  assert.deepEqual(feeds(graph, "step", "image"), { from: "a.image", to: "step.image" });

  // Typed values are read within the manifest's bounds before they reach the graph.
  assert.deepEqual(readControl(by.count, "12"), { problem: "Count is at most 9." });
  assert.deepEqual(readControl(by.count, "2.5"), { problem: "Count needs a whole number." });
  assert.deepEqual(readControl(by.mode, "slow"), { problem: "mode is one of fast, fine." });
  const typed = readControl(by.count, "7");
  assert.ok("value" in typed);

  const before = graph;
  graph = setParam(graph, "step", "count", typed.value);
  assert.equal(isSet(before, "step", "count"), false, "an edit leaves the graph it was given as it was");
  const set = Object.fromEntries(formModel(EVERY_PARAM, graph, "step").map((c) => [c.name, c]));
  assert.equal(set.count.set, true);
  assert.equal(set.count.value, 7);
  assert.deepEqual(graph.nodes.step.params, { count: 7 });

  graph = resetParam(graph, "step", "count");
  assert.deepEqual(graph.nodes.step.params, {}, "reset removes the value from the graph");
  assert.equal(formModel(EVERY_PARAM, graph, "step")[0].set, false);
});
