// @ts-check
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { EngineClient, EngineError, LineSplitter } from "../../app/main/engine.js";
import { RotatingLog } from "../../app/main/log.js";
import { ENGINE_METHODS } from "../../app/main/methods.js";
import { dataRoot, engineCommand, findUv } from "../../app/main/paths.js";

const here = path.dirname(fileURLToPath(import.meta.url));
const fake = () => new EngineClient({ command: process.execPath, args: [path.join(here, "fixtures", "fake-engine.js")],
  requestTimeoutMs: 2000 });

test("lines are rebuilt however the chunks are cut", () => {
  const s = new LineSplitter();
  assert.deepEqual(s.push('{"a":'), []);
  assert.deepEqual(s.push('1}\r\n{"b":2}\n{"c"'), ['{"a":1}', '{"b":2}']);
  assert.deepEqual(s.push(":3}\n\n"), ['{"c":3}']);
});

test("requests are answered by id, and events and logs arrive on their own channels", async () => {
  const client = fake();
  const logs = /** @type {string[]} */ ([]);
  const events = /** @type {any[]} */ ([]);
  client.on("log", (line) => logs.push(line));
  client.on("event", (e) => events.push(e));
  const ready = await client.start();
  assert.equal(ready.engine, "fake");
  assert.deepEqual(await client.request("echo", { x: 1 }), { x: 1 });
  assert.equal(await client.request("split"), "ok");
  assert.ok(events.some((e) => e.event === "run.start"));
  assert.equal(await client.request("noise"), "after noise");
  assert.equal(await client.request("null"), "after null"); // a JSON line that is not an object
  assert.ok(logs.some((l) => l.includes("not an object")));
  await assert.rejects(client.request("fail"), (/** @type {any} */ error) =>
    error instanceof EngineError && error.problems[0].node === "x");
  await client.stop();
  assert.ok(logs.includes("fake engine starting"));
  assert.ok(logs.some((l) => l.includes("not JSON")));
});

test("pending requests fail when the engine dies, and silence times out", async () => {
  const client = fake();
  await client.start();
  await assert.rejects(client.request("silent"), /did not answer silent/);
  const exited = new Promise((resolve) => client.once("exit", resolve));
  await assert.rejects(client.request("die"), /stopped while answering die/);
  assert.equal(/** @type {any} */ (await exited).code, 7);
  await assert.rejects(client.request("echo"), /not running/);
});

test("a reply after its request timed out is logged, never passed on as an event", async () => {
  const client = new EngineClient({ command: process.execPath, args: [path.join(here, "fixtures", "fake-engine.js")],
    requestTimeoutMs: 100 });
  const logs = /** @type {string[]} */ ([]);
  const events = /** @type {any[]} */ ([]);
  client.on("log", (line) => logs.push(line));
  client.on("event", (e) => events.push(e));
  await client.start();
  try {
    await assert.rejects(client.request("late"), /did not answer late within 100 ms/);
    await new Promise((resolve) => setTimeout(resolve, 600));
    assert.deepEqual(events.filter((e) => "id" in e), []);
    assert.ok(logs.some((l) => l.includes("after it had timed out")));
  } finally {
    await client.stop();
  }
});

test("the data root is one folder per platform, overridable", () => {
  // Every case is in contracts/layout.json (layout.test.js); these are the everyday ones.
  assert.equal(dataRoot({ ONEFRAME_DATA: "/x/y" }, "linux", "/home/a", false), "/x/y");
  assert.equal(dataRoot({ LOCALAPPDATA: "C:\\Users\\a\\AppData\\Local" }, "win32", "C:\\Users\\a", true),
    "C:\\Users\\a\\AppData\\Local\\OneframeLab");
  assert.equal(dataRoot({ XDG_DATA_HOME: "/d" }, "linux", "/home/a", false), "/d/oneframe-lab-dev");
  const cmd = engineCommand("uv", "/data");
  assert.equal(cmd.env.UV_CACHE_DIR, path.join("/data", "uv", "cache"));
  // uv writes nothing outside the data root: no python link in a bin folder, no registry entry.
  assert.equal(cmd.env.UV_PYTHON_INSTALL_BIN, "0");
  assert.equal(cmd.env.UV_PYTHON_INSTALL_REGISTRY, "0");
  assert.ok(cmd.args.includes("--frozen"), "the engine runs exactly what engine/uv.lock says");
});

test("the log rolls over and keeps a bounded number of files", () => {
  const dir = mkdtempSync(path.join(tmpdir(), "oneframe-log-"));
  try {
    const log = new RotatingLog(path.join(dir, "app.log"), { maxBytes: 100, keep: 2 });
    for (let i = 0; i < 20; i++) log.write("t", `line ${i} ${"x".repeat(20)}`);
    assert.deepEqual(readdirSync(dir).sort(), ["app.1.log", "app.2.log", "app.log"]);
    assert.match(readFileSync(path.join(dir, "app.log"), "utf8"), /line 19/);
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});

const uv = findUv();
const synced = uv !== null && spawnSync(uv, ["--version"]).status === 0;

test("the real engine starts from its lock file and lists the built-in nodes", { skip: !synced && "uv not found" },
  async () => {
    const data = mkdtempSync(path.join(tmpdir(), "oneframe-data-"));
    try {
      const client = new EngineClient({ ...engineCommand(/** @type {string} */ (uv), data), requestTimeoutMs: 120_000 });
      try {
        const ready = await client.start();
        assert.match(ready.python, /^3\.14\./);
        const { nodes } = await client.request("nodes.list");
        assert.ok(nodes.some((/** @type {any} */ n) => n.id === "source.image"));
        // Every method the page may call is one the engine has.
        const missing = [...ENGINE_METHODS].filter((m) => !ready.methods.includes(m));
        assert.deepEqual(missing, [], "the page's allowlist names methods the engine does not have");
      } finally {
        await client.stop();
      }
    } finally {
      rmSync(data, { recursive: true, force: true });
    }
  });
