// @ts-check
// The page's models (spec 017), checked without a browser: each is a plain module under
// app/renderer/ that uses neither Node nor the DOM, so these tests import it as it is.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { formModel, readControl } from "../../app/renderer/form.js";
import { feeds, isSet, openGraph, resetParam, setParam } from "../../app/renderer/graph-model.js";
import { readNpz } from "../../app/renderer/npz.js";
import { KINDS, offerFor, runView, starting } from "../../app/renderer/run-view.js";
import { REPO_ROOT } from "../../app/main/paths.js";

/** @typedef {import("../../app/protocol.js").Page.Manifest} Manifest */

const FIXTURES = path.join(path.dirname(fileURLToPath(import.meta.url)), "fixtures");

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

test("the npz reader reads what numpy writes", async () => {
  // The arrays tests/app/fixtures/npz/README.md says numpy was given.
  const points = Float32Array.from({ length: 36 }, (_, i) => i / 4 - 2);
  const valid = Uint8Array.from({ length: 12 }, (_, i) => (i % 3 !== 0 ? 1 : 0));
  for (const name of ["stored.npz", "deflated.npz"]) {
    const bytes = readFileSync(path.join(FIXTURES, "npz", name));
    const arrays = await readNpz(bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength));
    assert.deepEqual([...arrays.keys()].sort(), ["points", "valid"], name);
    const p = /** @type {import("../../app/renderer/npz.js").NpyArray} */ (arrays.get("points"));
    const v = /** @type {import("../../app/renderer/npz.js").NpyArray} */ (arrays.get("valid"));
    assert.equal(p.dtype, "<f4", name);
    assert.deepEqual(p.shape, [3, 4, 3], name);
    assert.ok(p.data instanceof Float32Array, name);
    assert.deepEqual([...p.data], [...points], name);
    assert.equal(v.dtype, "|b1", name);
    assert.deepEqual(v.shape, [3, 4], name);
    assert.deepEqual([...v.data], [...valid], name);
  }
  await assert.rejects(readNpz(new ArrayBuffer(64)), /Not an npz numpy wrote/);
});

test("a failure offers what it needs", () => {
  // Every row of architecture.md's table of failures: its kind, reason and retry.
  const doc = readFileSync(path.join(REPO_ROOT, "docs", "architecture.md"), "utf8");
  const table = doc.slice(doc.indexOf("## Failures"), doc.indexOf("## What comes next"));
  const rows = [...table.matchAll(/^\| `([a-z_]+)` \| (?:`([a-z_]+)`)? *\| (yes|no) \|/gm)]
    .map(([, kind, reason, retry]) => ({ kind, reason, retry: retry === "yes" }));
  assert.ok(rows.length > 60, `the table's rows were found (${rows.length})`);
  assert.deepEqual([...KINDS].sort(), [...new Set(rows.map((r) => r.kind))].sort(), "the page knows every kind, and no other");

  /** Requirement 5, row by row. @param {{ kind: string, reason?: string, retry: boolean }} row */
  const wanted = ({ kind, reason, retry }) => {
    const key = `${kind}/${reason ?? ""}`;
    if (key === "runtime/not_installed" || key === "runtime/out_of_date") return { action: "install" };
    if (key === "files/not_downloaded" || key === "files/incomplete") return { action: "download" };
    if (key === "runtime/installing") return { action: "wait", for: "runtime" };
    if (key === "files/downloading") return { action: "wait", for: "files" };
    return retry ? { action: "retry" } : null;
  };
  for (const row of rows) assert.deepEqual(offerFor(row), wanted(row), `${row.kind}/${row.reason ?? ""}`);
  const offered = new Set(rows.map((r) => offerFor(r)?.action ?? "none"));
  assert.deepEqual([...offered].sort(), ["download", "install", "none", "retry", "wait"]);

  for (const retry of [true, false]) {
    assert.equal(offerFor({ kind: "no_such_kind", retry }), null, "a kind the page does not know gets none");
    assert.equal(offerFor({ kind: "no_such_kind", reason: "not_installed", retry }), null);
  }

  // A card carries its failure's offer, from the run's events.
  let state = runView(starting(), { event: "run.start", run: "r1", order: ["a", "b"], notes: [] });
  state = runView(state, {
    event: "node.failed", run: "r1", step: "a", node: "x.y", kind: "runtime", reason: "not_installed",
    message: "It is not installed.", retry: false,
  });
  state = runView(state, { event: "node.skipped", run: "r1", step: "b", node: "x.z", waited_on: "a" });
  state = runView(state, { event: "node.failed", run: "other", step: "b", node: "x.z", kind: "node", message: "", retry: false });
  assert.deepEqual(state.cards.a.offer, { action: "install" });
  assert.equal(state.cards.b.phase, "skipped", "another run's events change nothing");
});

test("every viewer is for a port type that exists", () => {
  const types = Object.keys(JSON.parse(readFileSync(path.join(REPO_ROOT, "contracts", "port-types.json"), "utf8")).types);
  const folder = path.join(REPO_ROOT, "app", "renderer", "viewers");
  const modules = readdirSync(folder).filter((name) => name.endsWith(".js")).sort();
  assert.ok(modules.length >= 4, `the viewers were found (${modules.join(", ")})`);
  /** @type {string[]} */
  const named = [];
  for (const name of modules) {
    // Read, not imported: a viewer draws with the DOM and three.js, which these tests do not load.
    const source = readFileSync(path.join(folder, name), "utf8");
    const type = /^export const TYPE = "([^"]+)";$/m.exec(source)?.[1];
    assert.ok(type !== undefined, `${name} says which type it shows (export const TYPE = "...")`);
    assert.ok(types.includes(type), `${name} shows ${type}, which contracts/port-types.json does not declare`);
    named.push(type);
  }
  assert.equal(new Set(named).size, named.length, "one viewer a type");
  for (const type of ["Image", "Depth", "PointMap", "Camera"]) assert.ok(named.includes(type), `a viewer for ${type}`);
});
