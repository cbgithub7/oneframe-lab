// @ts-check
// Stop: before Claude ends a turn in which anything changed, run the project's checks (checks.js:
// the engine's in its fast tier). A failure sends Claude back to fix it, with the failing output
// as the reason.
//
// Skipped when nothing changed (a question answered, a file read), and when the tree is exactly
// as it was the last time the checks passed in this session. After three blocked stops in a row
// it lets the turn end with a warning instead, so an unfixable failure cannot loop forever; the
// PR's CI still stands in the way.

import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { STOP_CHECKS } from "./checks.js";
import { envWithPinnedTools } from "./pinned-tools.js";
import { run as runIn, treeFingerprint } from "./tree.js";

const ROOT = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
const ENV = envWithPinnedTools(ROOT);
const MAX_BLOCKS = 3;

const run = (/** @type {string} */ command, /** @type {string[]} */ args) => runIn(ROOT, ENV, command, args);

/** @type {any} */
let input = {};
try {
  input = JSON.parse(readFileSync(0, "utf8") || "{}");
} catch {
  // run the checks anyway
}
const session = String(input.session_id ?? "local").replace(/[^\w-]/g, "");
const stateFile = path.join(tmpdir(), `oneframe-stop-${session}.json`);
/** @type {{ passed?: string, blocks?: number }} */
let state = {};
try {
  state = JSON.parse(readFileSync(stateFile, "utf8"));
} catch {
  // first stop of the session
}
const save = () => writeFileSync(stateFile, JSON.stringify(state));

const status = run("git", ["status", "--porcelain"]).out;
const ahead = run("git", ["rev-list", "--count", "@{upstream}..HEAD"]);
const unpushed = ahead.ok && Number.parseInt(ahead.out, 10) > 0;
if (!status.trim() && !unpushed) process.exit(0);

const fingerprint = treeFingerprint(ROOT, ENV);
if (state.passed === fingerprint) process.exit(0);

for (const [label, command, args] of STOP_CHECKS) {
  const result = run(command, [...args]);
  if (result.ok) continue;
  state.blocks = (state.blocks ?? 0) + 1;
  save();
  const tail = result.out.split("\n").filter((l) => !l.includes("UV_NATIVE_TLS")).slice(-60).join("\n");
  if (state.blocks > MAX_BLOCKS) {
    process.stdout.write(JSON.stringify({ systemMessage: `${label} is still failing after ${MAX_BLOCKS} attempts; stopping anyway. Say so plainly in the reply.` }));
    process.exit(0);
  }
  process.stdout.write(JSON.stringify({ decision: "block", reason: `${label} fails. Fix it before finishing (do not weaken or delete tests):\n${tail}` }));
  process.exit(0);
}
state = { passed: fingerprint, blocks: 0 };
save();
