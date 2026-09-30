// @ts-check
// Stop: before Claude ends a turn in which anything changed, run the project's checks. A failure
// sends Claude back to fix it, with the failing output as the reason.
//
// Skipped when nothing changed (a question answered, a file read), and when the tree is exactly
// as it was the last time the checks passed in this session. After three blocked stops in a row
// it lets the turn end with a warning instead, so an unfixable failure cannot loop forever; the
// PR's CI still stands in the way.

import { createHash } from "node:crypto";
import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { envWithPinnedTools } from "./pinned-tools.js";

const ROOT = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();
const ENV = envWithPinnedTools(ROOT);
const MAX_BLOCKS = 3;

/**
 * Run a command. On Windows only npm and .cmd shims need a shell; everything else runs directly,
 * so a project path with spaces in it works.
 * @param {string} command @param {string[]} args
 */
function run(command, args) {
  const shell = process.platform === "win32" && (command === "npm" || command.endsWith(".cmd"));
  const quote = (/** @type {string} */ s) => (shell && /\s/.test(s) ? `"${s}"` : s);
  const r = spawnSync(quote(command), args.map(quote), { cwd: ROOT, env: ENV, encoding: "utf8", shell, maxBuffer: 64 * 1024 * 1024 });
  return { ok: r.status === 0, out: `${r.stdout ?? ""}${r.stderr ?? ""}` };
}

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

const head = run("git", ["rev-parse", "HEAD"]).out.trim();
const diff = run("git", ["diff", "HEAD"]).out;
const fingerprint = createHash("sha256").update(head).update(status).update(diff).digest("hex");
if (state.passed === fingerprint) process.exit(0);

const checks = [
  ["npm run check", "npm", ["run", "--silent", "check"]],
  ["npm run engine:check", "npm", ["run", "--silent", "engine:check"]],
  ["test guard", "node", ["scripts/test-guard.js"]],
];
for (const [label, command, args] of /** @type {Array<[string, string, string[]]>} */ (checks)) {
  const result = run(command, args);
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
