// @ts-check
// Where things live. Everything the app writes goes under one data root the person can see and
// remove: the node cache, model weights, runtimes, uv's own cache and Pythons, and logs.

import { existsSync } from "node:fs";
import { homedir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

/** The folder holding app/, engine/, contracts/ and nodes/ in development. */
export const REPO_ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

/**
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 */
export function dataRoot(env = process.env, platform = process.platform) {
  if (env.ONEFRAME_DATA) return path.resolve(env.ONEFRAME_DATA);
  if (platform === "win32") return path.join(env.LOCALAPPDATA ?? path.join(homedir(), "AppData", "Local"), "OneframeLab");
  return path.join(env.XDG_DATA_HOME ?? path.join(homedir(), ".local", "share"), "oneframe-lab");
}

/**
 * Find uv: ONEFRAME_UV, then PATH. The packaged app will carry its own pinned uv; until then a
 * developer's uv is used and the engine project pins the minimum version it accepts.
 * @param {NodeJS.ProcessEnv} [env]
 * @param {NodeJS.Platform} [platform]
 * @returns {string | null}
 */
export function findUv(env = process.env, platform = process.platform) {
  if (env.ONEFRAME_UV) return env.ONEFRAME_UV;
  const exe = platform === "win32" ? "uv.exe" : "uv";
  const dirs = (env.PATH ?? env.Path ?? "").split(path.delimiter).filter(Boolean);
  const extra = [path.join(homedir(), ".local", "bin"), path.join(homedir(), ".cargo", "bin")];
  for (const dir of [...dirs, ...extra]) {
    const candidate = path.join(dir, exe);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

/**
 * The command that starts the engine in development: uv runs it from the locked project, so the
 * interpreter and every package are exactly what engine/uv.lock says.
 * @param {string} uv
 * @param {string} data
 */
export function engineCommand(uv, data) {
  const env = {
    ...process.env,
    ONEFRAME_ROOT: REPO_ROOT,
    // uv keeps its package cache and downloaded Pythons under the data root, so removing the data
    // root removes them too, and hard links between cache and runtimes stay on one volume.
    UV_CACHE_DIR: path.join(data, "uv", "cache"),
    UV_PYTHON_INSTALL_DIR: path.join(data, "uv", "python"),
  };
  return {
    command: uv,
    args: ["run", "--project", path.join(REPO_ROOT, "engine"), "--frozen", "python", "-m", "oneframe.server",
      "--data", data],
    cwd: REPO_ROOT,
    env,
  };
}
