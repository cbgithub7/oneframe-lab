// @ts-check
// Starts a command-line tool that opens a data root, the way the app starts the engine: uv runs it
// from the locked engine project, with uv's own temporary files (its lock, for one) kept under the
// tool's data root, in `tools_tmp`, rather than in the system temporary folder. Every npm script
// that opens a data root goes through here (spec 003, task 8):
//
//   node scripts/tool.js <module> [args...]      e.g. oneframe.bench_fit test.vram --set n=1
//
// The data root is the one the tool will open: `--data <folder>` when given, else ONEFRAME_DATA,
// else the dev root. Ctrl+C reaches the tool itself, which treats it as a Stop; this script waits
// for the tool to end and exits with its code.

import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { REPO_ROOT, dataRoot, findUv, layout } from "../app/main/paths.js";

/**
 * The data root the tool will open.
 * @param {string[]} args the tool's own arguments
 * @param {NodeJS.ProcessEnv} env
 * @param {string} cwd where relative paths start, as for the tool
 * @param {string} [platform]
 * @param {string} [home]
 */
export function toolRoot(args, env, cwd, platform = process.platform, home = homedir()) {
  const at = args.indexOf("--data");
  const given = at >= 0 ? args[at + 1] : args.find((a) => a.startsWith("--data="))?.slice("--data=".length);
  if (given) return path.resolve(cwd, given);
  return dataRoot(env, platform, home, false);
}

/**
 * The command that runs `module` with `args`, and the temporary folder uv is given.
 * @param {string} uv
 * @param {string} module
 * @param {string[]} args
 * @param {NodeJS.ProcessEnv} [env]
 * @param {string} [cwd]
 */
export function toolCommand(uv, module, args, env = process.env, cwd = process.cwd()) {
  const tmp = layout(toolRoot(args, env, cwd)).tools_tmp;
  /** @type {NodeJS.ProcessEnv} */
  const childEnv = { ...env, TMP: tmp, TEMP: tmp, TMPDIR: tmp };
  return {
    command: uv,
    args: ["run", "--project", path.join(REPO_ROOT, "engine"), "--frozen", "python", "-B", "-m", module, ...args],
    env: childEnv,
    tmp,
  };
}

function main() {
  const [module, ...args] = process.argv.slice(2);
  if (!module) {
    console.error("usage: node scripts/tool.js <module> [args...]");
    return process.exit(2);
  }
  const uv = findUv();
  if (!uv) {
    console.error("uv was not found: install it, or set ONEFRAME_UV to its path.");
    return process.exit(2);
  }
  const { command, args: argv, env, tmp } = toolCommand(uv, module, args);
  mkdirSync(tmp, { recursive: true });
  const child = spawn(command, argv, { stdio: "inherit", env });
  // Ctrl+C goes to the tool too, which stops and says so; this waits for it rather than leaving it.
  process.on("SIGINT", () => {});
  child.on("error", (error) => {
    console.error(`could not start uv: ${error.message}`);
    process.exit(2);
  });
  child.on("exit", (code, signal) => process.exit(code ?? (signal ? 130 : 1)));
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) main();
