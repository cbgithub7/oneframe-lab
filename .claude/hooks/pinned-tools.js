// @ts-check
// The Node and uv this repo pins, for the hooks to put first on PATH.
//
// The start hook (session-start.sh) installs them under ~/.local/share/oneframe-tools, but a
// hook does not see the PATH it exported: a resumed web session, or a hook run on its own, finds
// the container's older uv, and `uv run` refuses it. The versions are read from the repo's own
// files, as the start hook reads them, so a bump needs no edit here. Where nothing is installed
// (a developer's own machine, a test project) the environment is returned as it was.

import { existsSync, readFileSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";

/**
 * The bin folders of the pinned Node and uv that exist, Node first.
 * @param {string} root  The project folder.
 * @param {string} [home]
 * @returns {string[]}
 */
export function pinnedToolDirs(root, home = homedir()) {
  const tools = path.join(home, ".local", "share", "oneframe-tools");
  /** @param {string} file */
  const read = (file) => {
    try {
      return readFileSync(path.join(root, file), "utf8");
    } catch {
      return "";
    }
  };
  const node = read(".node-version").trim();
  const uv = read("engine/pyproject.toml").match(/^required-version\s*=\s*">=([\d.]+)"/m)?.[1];
  const dirs = [];
  if (node) dirs.push(path.join(tools, `node-v${node}`, "bin"));
  if (uv) dirs.push(path.join(tools, `uv-${uv}`));
  return dirs.filter((dir) => existsSync(dir));
}

/**
 * `env` with the pinned tools first on its PATH (whatever the variable's case, for Windows).
 * @param {string} root
 * @param {NodeJS.ProcessEnv} [env]
 * @param {string} [home]
 * @returns {NodeJS.ProcessEnv}
 */
export function envWithPinnedTools(root, env = process.env, home) {
  const dirs = pinnedToolDirs(root, home);
  if (!dirs.length) return env;
  const key = Object.keys(env).find((name) => name.toLowerCase() === "path") ?? "PATH";
  return { ...env, [key]: [...dirs, env[key] ?? ""].filter(Boolean).join(path.delimiter) };
}
