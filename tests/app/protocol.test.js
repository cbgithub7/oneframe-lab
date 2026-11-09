// @ts-check
// The page's types are generated from the contract (spec 018): app/protocol.d.ts is what
// scripts/protocol-types.js writes from contracts/protocol.json, and the check fails, naming the
// difference, when either changes without the other.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";

import { CONTRACT_FILE } from "../../app/main/protocol.js";
import { REPO_ROOT } from "../../app/main/paths.js";
import { TYPES_FILE } from "../../scripts/protocol-types.js";

const SCRIPT = path.join(REPO_ROOT, "scripts", "protocol-types.js");

/** @param {string[]} args */
const types = (...args) => spawnSync(process.execPath, [SCRIPT, ...args], { encoding: "utf8" });

test("the page's types are the contract's", () => {
  const committed = types();
  assert.equal(committed.status, 0, committed.stderr);
  assert.match(committed.stdout, /ok/);

  const dir = mkdtempSync(path.join(tmpdir(), "oneframe-protocol-"));
  try {
    const contract = JSON.parse(readFileSync(CONTRACT_FILE, "utf8"));
    const write = (/** @type {string} */ name, /** @type {unknown} */ data) => {
      const file = path.join(dir, name);
      writeFileSync(file, JSON.stringify(data));
      return file;
    };
    // A field changed: the check fails and names the line, as the contract gives it and as the file has it.
    const changed = structuredClone(contract);
    changed.events["run.start"].fields.notes = { type: "integer" };
    const field = types("--contract", write("changed.json", changed));
    assert.equal(field.status, 1);
    assert.match(field.stderr, /protocol-types\.js --write/, "it says how to bring the file up to date");
    assert.match(field.stderr, /^\+ \d+: notes: number;$/m);
    assert.match(field.stderr, /^- \d+: notes: Array<string>;$/m);
    // An event added: it is named.
    const added = structuredClone(contract);
    added.events["run.paused"] = { doc: "A run paused.", by: ["engine"], fields: { run: { type: "string" } } };
    const event = types("--contract", write("added.json", added));
    assert.equal(event.status, 1);
    assert.match(event.stderr, /^\+ \d+: export interface RunPausedEvent \{$/m);
    // What --write writes is what the check then finds current.
    const file = path.join(dir, "protocol.d.ts");
    assert.equal(types("--contract", write("added.json", added), "--types", file, "--write").status, 0);
    const round = types("--contract", write("added.json", added), "--types", file);
    assert.equal(round.status, 0, round.stderr);
    assert.notEqual(readFileSync(file, "utf8"), readFileSync(TYPES_FILE, "utf8"));
  } finally {
    rmSync(dir, { recursive: true, force: true });
  }
});
