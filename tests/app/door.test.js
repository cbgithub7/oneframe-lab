// @ts-check
// The one door into the page answers with values, failures included, so a failure's kind, reason
// and next reach the page (AC6 of spec 006), and every failure the app reports is one the engine's
// declaration names (docs/architecture.md's table, held equal to oneframe/errors.py by a Python test).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { answer } from "../../app/main/door.js";
import { EngineClient } from "../../app/main/engine.js";
import { APP_FAILURES } from "../../app/main/failures.js";
import { REPO_ROOT } from "../../app/main/paths.js";

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
});

test("every failure the app reports is one the declaration names, with the same retry", () => {
  const rows = table();
  assert.ok(rows.size > 10, "the failure table was found");
  for (const [name, failure] of Object.entries(APP_FAILURES)) {
    assert.equal(rows.get(`${failure.kind}/${failure.reason}`), failure.retry, name);
  }
});
