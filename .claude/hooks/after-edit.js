// @ts-check
// PostToolUse: format and lint-fix the file just edited, and report what is still wrong.
//
// Python: ruff format, then ruff check --fix. JavaScript: eslint --fix. Anything left that the
// tools cannot fix is sent back to Claude (exit 2) so it is dealt with while the edit is fresh,
// not discovered at the end of the turn.

import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";

const ROOT = process.env.CLAUDE_PROJECT_DIR ?? process.cwd();

/**
 * Run a command. On Windows only npm and .cmd shims need a shell; everything else runs directly,
 * so a project path with spaces in it works.
 * @param {string} command @param {string[]} args
 */
function run(command, args) {
  const shell = process.platform === "win32" && (command === "npm" || command.endsWith(".cmd"));
  const quote = (/** @type {string} */ s) => (shell && /\s/.test(s) ? `"${s}"` : s);
  const r = spawnSync(quote(command), args.map(quote), { cwd: ROOT, encoding: "utf8", shell, maxBuffer: 64 * 1024 * 1024 });
  return { ok: r.status === 0, out: `${r.stdout ?? ""}${r.stderr ?? ""}`.trim() };
}

let input = {};
try {
  input = JSON.parse(readFileSync(0, "utf8") || "{}");
} catch {
  process.exit(0);
}
const file = /** @type {any} */ (input).tool_input?.file_path;
if (typeof file !== "string" || !existsSync(file)) process.exit(0);
const rel = path.relative(ROOT, path.resolve(file));
if (rel.startsWith("..") || /(^|[\\/])(node_modules|\.venv)[\\/]/.test(rel)) process.exit(0);

const problems = [];
if (rel.endsWith(".py")) {
  run("uv", ["run", "--project", "engine", "--frozen", "ruff", "format", rel]);
  const check = run("uv", ["run", "--project", "engine", "--frozen", "ruff", "check", "--fix", rel]);
  if (!check.ok) problems.push(check.out);
} else if (/\.(c|m)?js$/.test(rel)) {
  const eslint = path.join(ROOT, "node_modules", ".bin", process.platform === "win32" ? "eslint.cmd" : "eslint");
  if (existsSync(eslint)) {
    const check = run(eslint, ["--fix", rel]);
    if (!check.ok) problems.push(check.out);
  }
}
if (problems.length) {
  process.stderr.write(`Lint problems remain in ${rel}:\n${problems.join("\n").split("\n").filter((l) => !l.includes("UV_NATIVE_TLS")).slice(-40).join("\n")}\n`);
  process.exit(2);
}
