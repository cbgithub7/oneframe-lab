// @ts-check
// The data root and its layout, against contracts/layout.json, the table the engine's tests read
// too (AC1 of spec 006).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";

import { REPO_ROOT, absolute, dataRoot, layout } from "../../app/main/paths.js";

/** @type {{ roots: { case: string, platform: string, home: string, packaged: boolean,
 *   env: Record<string, string>, root: string }[], paths: Record<string, string> }} */
const TABLE = JSON.parse(readFileSync(path.join(REPO_ROOT, "contracts", "layout.json"), "utf8"));

test("every row gives the root the table names", () => {
  for (const row of TABLE.roots) {
    const got = dataRoot(row.env, row.platform, row.home, row.packaged);
    assert.equal(got, row.root, row.case);
    // The answer is already in the platform's own spelling: its path flavour leaves it unchanged.
    const flavour = row.platform === "win32" ? path.win32 : path.posix;
    assert.equal(flavour.resolve(got), got, row.case);
  }
});

test("only absolute values count, on either platform", () => {
  assert.equal(absolute("C:x", "win32"), null);
  assert.equal(absolute("\\x", "win32"), null);
  assert.equal(absolute("//server", "win32"), null); // a share needs its name
  assert.equal(absolute("/x", "win32"), null);
  assert.equal(absolute("C:\\", "win32"), "C:\\");
  assert.equal(absolute("x/y", "linux"), null);
  assert.equal(absolute("/", "linux"), "/");
  assert.equal(absolute("/a/../../b", "linux"), "/b"); // never above the root
});

test("a home that is not absolute is refused rather than guessed", () => {
  assert.throws(() => dataRoot({}, "linux", "ana", false), /not an absolute path/);
});

test("the layout names the paths the table names", () => {
  const root = path.join("root");
  const named = layout(root);
  assert.deepEqual(Object.keys(named).sort(), Object.keys(TABLE.paths).sort());
  for (const [name, relative] of Object.entries(TABLE.paths)) {
    assert.equal(named[/** @type {keyof typeof named} */ (name)], path.join(root, ...relative.split("/")), name);
  }
});
