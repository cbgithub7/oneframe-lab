// @ts-check
// The one door into the page answers with values, failures included, so a failure's kind, reason
// and next reach the page (AC6 of spec 006), and every failure the app reports is one the engine's
// declaration names (docs/architecture.md's table, held equal to oneframe/errors.py by a Python test).
// No path reaches the page through it (spec 016).
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { homedir, tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { answer, forPage } from "../../app/main/door.js";
import { EngineClient } from "../../app/main/engine.js";
import { APP_FAILURES, appFailure } from "../../app/main/failures.js";
import { APP_METHODS, ENGINE_METHODS } from "../../app/main/methods.js";
import { REPO_ROOT, engineCommand, findUv } from "../../app/main/paths.js";
import { problems } from "../../app/main/protocol.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const PAGE = "file:///app/renderer/index.html";

/** Rows of the failure table in docs/architecture.md: kind, reason, retry. */
function table() {
  const text = readFileSync(path.join(REPO_ROOT, "docs", "architecture.md"), "utf8");
  const rows = new Map();
  for (const line of text.split("\n")) {
    const cells = line.split("|").slice(1, -1).map((c) => c.trim().replaceAll("`", ""));
    if (cells.length !== 4 || !/^[a-z_]+$/.test(cells[0]) || !["yes", "no"].includes(cells[2])) continue;
    rows.set(`${cells[0]}/${cells[1]}`, cells[2] === "yes");
  }
  return rows;
}

test("an error reply's kind, reason and next reach the page as a value", async () => {
  const engine = new EngineClient({ command: process.execPath, args: [path.join(here, "fixtures", "fake-engine.js")],
    requestTimeoutMs: 1000 });
  await engine.start();
  try {
    assert.deepEqual(await answer(engine, PAGE, PAGE, "runtimes.install", { runtime: "tiny" }), { ok: false,
      error: { kind: "runtime", reason: "not_installed", message: "The runtime 'tiny' is not installed.",
        next: "Install the runtime tiny.", retry: false } });
    // The fake never answers engine.hello: a timeout is a declared failure too.
    assert.deepEqual(await answer(engine, PAGE, PAGE, "engine.hello", {}), { ok: false,
      error: { kind: "request", reason: "timed_out", retry: true,
        message: "The engine did not answer engine.hello within 1000 ms." } });
  } finally {
    await engine.stop();
  }
});

test("the door refuses what the page may not ask, as values", async () => {
  const ready = { ready: true, request: async () => "fine" };
  assert.deepEqual(await answer(ready, PAGE, PAGE, "nodes.list", {}), { ok: true, result: "fine" });
  for (const [sender, method, params] of /** @type {Array<[string, unknown, unknown]>} */ ([
    ["file:///elsewhere.html", "nodes.list", {}],
    [PAGE, "shell.exec", {}],
    [PAGE, 7, {}],
    [PAGE, "nodes.list", []],
    [PAGE, "nodes.list", null],
  ])) {
    const got = await answer(ready, sender, PAGE, method, params);
    assert.equal(got.ok, false);
    assert.equal(!got.ok && got.error.reason, "refused");
  }
  const down = await answer(null, PAGE, PAGE, "nodes.list", {});
  assert.deepEqual(down, { ok: false, error: { kind: "request", reason: "not_running", retry: true,
    message: "The engine is not running.", next: "Wait for it to start." } });
  // A page that subscribed after the engine failed to start learns why from its first request.
  const why = appFailure("no_uv", "uv was not found.", "Install uv, or set ONEFRAME_UV to its path.");
  assert.deepEqual(await answer(null, PAGE, PAGE, "engine.hello", {}, why), { ok: false, error: why });
});

test("every failure the app reports is one the declaration names, with the same retry", () => {
  const rows = table();
  assert.ok(rows.size > 10, "the failure table was found");
  for (const [name, failure] of Object.entries(APP_FAILURES)) {
    assert.equal(rows.get(`${failure.kind}/${failure.reason}`), failure.retry, name);
  }
});

test("photo.pick answers an import and a name, never a path", async () => {
  const picked = path.join(path.sep, "home", "ana", "Pictures", "holiday", "beach.jpg");
  const asked = /** @type {Array<[string, unknown]>} */ ([]);
  const engine = {
    ready: true,
    request: async (/** @type {string} */ method, /** @type {any} */ params) => {
      asked.push([method, params]);
      return { id: "ab".repeat(32), name: "beach.jpg", path: params.path };
    },
  };
  const places = { data: path.join(path.sep, "data"), repo: REPO_ROOT, home: path.join(path.sep, "home", "ana") };
  const got = await answer(engine, PAGE, PAGE, "photo.pick", {}, null, { pick: async () => picked, places });
  assert.deepEqual(got, { ok: true, result: { id: "ab".repeat(32), name: "beach.jpg" } });
  assert.deepEqual(asked, [["inputs.import", { path: picked }]], "the engine imports what was picked");
  for (const part of ["home", "ana", "Pictures", "holiday"]) assert.ok(!JSON.stringify(got).includes(part), part);
  // Nothing picked: no import, and nothing to show.
  assert.deepEqual(await answer(engine, PAGE, PAGE, "photo.pick", {}, null, { pick: async () => null, places }),
    { ok: true, result: null });
  assert.equal(asked.length, 1);
  // The page may not ask the engine to import a path itself.
  const refused = await answer(engine, PAGE, PAGE, "inputs.import", { path: picked }, null, { places });
  assert.equal(!refused.ok && refused.error.reason, "refused");
});

test("forPage drops local paths and names the root, the repo and the home folder", () => {
  const places = { data: "C:\\Users\\Ana\\AppData\\Local\\OneframeLab-dev", repo: "D:\\code\\oneframe-lab",
    home: "C:\\Users\\Ana" };
  // What the contract marks "page": false goes, wherever its declaration puts it (spec 018).
  const hello = { engine: "0.1.0", python: "3.15.1", warnings: [], nodes: 1, methods: ["engine.hello"] };
  assert.deepEqual(forPage({ event: "engine.ready", ...hello, data: places.data }, places),
    { event: "engine.ready", ...hello });
  const node = { id: "x", version: "1", title: "X", category: "test", files: [{ path: "weights/model.safetensors" }] };
  const listed = forPage({ ok: true, result: {
    nodes: [{ ...node, folder: "D:\\code\\oneframe-lab\\nodes\\x" }],
    problems: { "d:/code/Oneframe-Lab/nodes/bad/node.json": ["cannot read"] },
  } }, places, { method: "nodes.list" });
  assert.deepEqual(listed, { ok: true, result: { nodes: [node], problems: { "<repo>/nodes/bad/node.json": ["cannot read"] } } });
  const value = { type: "Depth", facets: { kind: "metric" }, meta: {}, trust: "predicted", key: "ab:depth" };
  const done = forPage({ event: "run.done", run: "r", outputs: { d: { depth: {
    ...value, path: "C:\\Users\\Ana\\AppData\\Local\\OneframeLab-dev\\cache\\objects\\ab\\x.npz" } } } }, places);
  assert.deepEqual(done, { event: "run.done", run: "r", outputs: { d: { depth: value } } });
  // Where the contract leaves a field free-form (a value's meta) or declares nothing, a field that
  // holds a local path goes, whatever its name.
  const made = forPage({ event: "run.done", run: "r", outputs: { d: { depth: { ...value,
    meta: { source: "E:\\photos\\beach.jpg", width: 4, name: "beach.jpg" } } } } }, places);
  assert.deepEqual(made, { event: "run.done", run: "r", outputs: { d: { depth: { ...value, meta: { width: 4, name: "beach.jpg" } } } } });
  assert.deepEqual(forPage({ event: "run.paused", run: "r", where: "/srv/photos/beach.jpg", step: "s" }, places),
    { event: "run.paused", run: "r", step: "s" });
  // Every other string has the root, the repo and the home folder by name.
  const failed = forPage({ ok: false, error: { kind: "engine", message: "Boom.", retry: false,
    detail: "File \"C:\\Users\\Ana\\AppData\\Local\\OneframeLab-dev\\x.py\", and C:/Users/Ana/notes.txt, not C:\\Users\\Anabel",
  } }, places, { method: "nodes.list" });
  assert.deepEqual(failed, { ok: false, error: { kind: "engine", message: "Boom.", retry: false,
    detail: "File \"<data>\\x.py\", and ~/notes.txt, not C:\\Users\\Anabel" } });
});

const uv = findUv();
const synced = uv !== null && spawnSync(uv, ["--version"]).status === 0;

/**
 * Every string and key in `value`.
 * @param {unknown} value
 * @returns {Generator<string>}
 */
function* texts(value) {
  if (typeof value === "string") yield value;
  else if (Array.isArray(value)) for (const item of value) yield* texts(item);
  else if (value !== null && typeof value === "object") {
    for (const [key, item] of Object.entries(value)) {
      yield key;
      yield* texts(item);
    }
  }
}

/** @param {string} folder @param {Record<string, unknown>} manifest @param {string} code */
function writeNode(folder, manifest, code) {
  mkdirSync(folder, { recursive: true });
  writeFileSync(path.join(folder, "node.json"), JSON.stringify(manifest));
  writeFileSync(path.join(folder, "node.py"), code);
}

/** 016's test graph: a source, and a node that fails on what it makes. */
const GRAPH = { version: 1, nodes: { s: { node: "test.source" }, f: { node: "test.fails" } },
  edges: [{ from: "s.text", to: "f.text" }] };
/** The params each method the page may ask is asked with; {} for the rest. */
/** @type {Record<string, Record<string, unknown>>} */
const ASKING = {
  "nodes.fit": { node: "test.fails" },
  "graph.validate": { graph: GRAPH },
  "graph.fits": { graph: GRAPH, port: "s.text" },
  "graphs.get": { name: "first-light" },
  "graphs.save": { name: "mine", graph: GRAPH },
  "graphs.remove": { name: "mine" },
  "runtimes.plan": { runtime: "no.such" },
  "runtimes.install": { runtime: "no.such" },
  "runtimes.stop": { runtime: "no.such" },
  "runtimes.remove": { runtime: "no.such" },
  "models.download": { node: "test.source" },
};

/**
 * 016's test engine, not yet started: a source, a node that fails, one that waits until it is
 * stopped, and a broken manifest, all in the data root, so a trace through them names a folder
 * the door knows; and every event it says. `end` stops it and removes the root.
 */
function testEngine() {
  const data = mkdtempSync(path.join(tmpdir(), "oneframe-door-"));
  const nodes = path.join(data, "test-nodes");
  writeNode(path.join(nodes, "test.source"), { id: "test.source", version: "1", title: "S", category: "test",
    outputs: { text: "Text" } },
  "def run(ctx):\n    p = ctx.path('t.json')\n    p.write_text('1')\n    ctx.output('text', p)\n");
  writeNode(path.join(nodes, "test.fails"), { id: "test.fails", version: "1", title: "F", category: "test",
    inputs: { text: "Text" }, outputs: { text: "Text" } },
  "def run(ctx):\n    raise RuntimeError('this node fails, with a trace through its folder')\n");
  writeNode(path.join(nodes, "test.waits"), { id: "test.waits", version: "1", title: "W", category: "test",
    outputs: { text: "Text" } },
  "import time\n\ndef run(ctx):\n    ctx.stage('waiting', 'until it is stopped')\n    while True:\n" +
    "        ctx.check_stop()\n        time.sleep(0.05)\n");
  mkdirSync(path.join(nodes, "broken"));
  writeFileSync(path.join(nodes, "broken", "node.json"), "{ not json");
  const places = { data, repo: REPO_ROOT, home: homedir() };
  const spec = engineCommand(/** @type {string} */ (uv), data);
  const client = new EngineClient({ ...spec, args: [...spec.args, "--nodes", nodes], requestTimeoutMs: 120_000 });
  const events = /** @type {any[]} */ ([]);
  client.on("event", (event) => events.push(event));
  const end = async () => {
    await client.stop();
    rmSync(data, { recursive: true, force: true });
  };
  return { data, places, client, events, end };
}

/**
 * Wait for `check` to hold, up to `ms`, failing with `what`.
 * @param {() => boolean} check @param {string} what @param {number} [ms]
 */
async function until(check, what, ms = 60_000) {
  const deadline = Date.now() + ms;
  while (!check()) {
    assert.ok(Date.now() < deadline, `waited ${ms} ms for ${what}`);
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
}

test("nothing the page receives holds a path", { skip: !synced && "uv not found", timeout: 300_000 }, async () => {
  const { data, places, client, events, end } = testEngine();
  const seen = /** @type {unknown[]} */ ([]);
  try {
    seen.push(forPage(await client.start(), places));
    for (const method of [...ENGINE_METHODS].filter((m) => m !== "graph.run")) {
      seen.push([method, await answer(client, PAGE, PAGE, method, ASKING[method] ?? {}, null, { places })]);
    }
    const run = await answer(client, PAGE, PAGE, "graph.run", { graph: GRAPH }, null, { places });
    assert.ok(run.ok, JSON.stringify(run));
    seen.push(run);
    const deadline = Date.now() + 60_000;
    while (!events.some((e) => e.event === "run.failed") && Date.now() < deadline) {
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
    assert.ok(events.some((e) => e.event === "node.failed"), "the run's node failed, with its trace");
    seen.push(...events.map((event) => forPage(event, places)));
  } finally {
    await end();
  }
  const kept = [data, REPO_ROOT, homedir()].flatMap((folder) => [folder, folder.replaceAll("\\", "/")]);
  for (const text of texts(seen)) {
    for (const folder of kept) {
      assert.ok(!text.toLowerCase().includes(folder.toLowerCase()), `${JSON.stringify(text)} names ${folder}`);
    }
  }
  // Without the door they would: the check finds what it looks for.
  assert.ok([...texts(events)].some((text) => text.includes(data)), "the engine's own events name the root");
});

test("what reaches the page is declared", { skip: !synced && "uv not found", timeout: 300_000 }, async () => {
  // Spec 018, AC5: every answer and event forPage passes on from the test engine (a broken
  // manifest, a node that fails, a run, a stop) is as contracts/protocol.json declares it, without
  // a field it marks as never reaching the page; and the door logged nothing.
  const { data, places, client, events, end } = testEngine();
  const logged = /** @type {string[]} */ ([]);
  const app = { places, log: (/** @type {string} */ line) => logged.push(line) };
  /** @type {Array<[string, unknown, string | undefined]>} what the page received: [what, message, method] */
  const seen = [];
  const ask = async (/** @type {string} */ method, /** @type {Record<string, unknown>} */ params, extra = {}) => {
    const got = await answer(client, PAGE, PAGE, method, params, null, { ...app, ...extra });
    seen.push([method, got, method]);
    return got;
  };
  const failing = { version: 1, nodes: { ...GRAPH.nodes, k: { node: "test.fails" } },
    edges: [...GRAPH.edges, { from: "f.text", to: "k.text" }] };
  const waiting = { version: 1, nodes: { w: { node: "test.waits" } }, edges: [] };
  try {
    seen.push(["engine.ready", forPage(await client.start(), places, { log: app.log }), undefined]);
    for (const method of [...ENGINE_METHODS].filter((m) => m !== "graph.run")) await ask(method, ASKING[method] ?? {});
    const photo = path.join(data, "photo.png");
    writeFileSync(photo, "a photo's bytes");
    for (const method of APP_METHODS) await ask(method, {}, { pick: async () => photo });
    assert.ok((await ask("graph.run", { graph: failing })).ok);
    await until(() => events.some((e) => e.event === "run.failed"), "the failing run's ending");
    assert.ok((await ask("graph.run", { graph: waiting })).ok);
    await until(() => events.some((e) => e.event === "stage" && e.step === "w"), "the waiting node's stage");
    assert.deepEqual(/** @type {any} */ (await ask("run.stop", {})).result.stopping, true);
    await until(() => events.some((e) => e.event === "run.stopped"), "the stopped run's ending");
    for (const event of events) seen.push([event.event, forPage(event, places, { log: app.log }), undefined]);
  } finally {
    await end();
  }
  const kinds = new Set(seen.map(([what]) => what));
  for (const kind of ["node.failed", "node.skipped", "run.failed", "stage", "run.stopped", "photo.pick"]) {
    assert.ok(kinds.has(kind), `the page received ${kind}`);
  }
  for (const [what, message, method] of seen) {
    assert.deepEqual(problems(message, { method, page: true }), [], `${what}: ${JSON.stringify(message).slice(0, 300)}`);
  }
  assert.deepEqual(logged, [], "the door found nothing undeclared");
  // A message the contract does not declare still goes, and is logged once by its name.
  const undeclared = { event: "run.paused", run: "abc" };
  assert.deepEqual(forPage(undeclared, places, { log: app.log }), undeclared);
  forPage(undeclared, places, { log: app.log });
  assert.equal(logged.length, 1);
  assert.match(logged[0], /"run\.paused" is not a declared event/);
});
